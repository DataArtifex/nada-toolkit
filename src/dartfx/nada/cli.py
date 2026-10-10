import csv
import json
import os
import sys
from enum import StrEnum
from typing import Annotated, Literal

import typer
from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.table import Table

from dartfx.nada.harvester import harvest as _harvest_cmd
from dartfx.nada.nada import (
    NadaServer,
    SearchParameters,
    ServerInstallation,
    fetch_nada_installations,
)

load_dotenv(find_dotenv(usecwd=True))


def _normalize_smart_dashes() -> None:
    """Sanitize macOS smart dashes (en-dash, em-dash) in sys.argv to standard hyphens."""
    normalized: list[str] = []
    for arg in sys.argv:
        if any(arg.startswith(prefix) for prefix in ("-–", "—", "–", "−")):
            clean = arg.replace("-–", "--").replace("—", "--").replace("−", "-")
            if clean.startswith("–"):
                clean = "--" + clean[1:] if len(clean) > 2 else "-" + clean[1:]
            normalized.append(clean)
        else:
            normalized.append(arg)
    sys.argv[:] = normalized


_normalize_smart_dashes()

app = typer.Typer(
    name="dartfx-nada",
    help="CLI for discovering and interacting with IHSN NADA repositories.",
    add_completion=False,
)
console = Console()


class OutputFormat(StrEnum):
    TABLE = "table"
    JSON = "json"
    CSV = "csv"


def get_server(hostname: str | None = None, api_key: str | None = None) -> NadaServer:
    """Helper to create a NadaServer instance."""
    resolved_host = hostname or os.environ.get("NADA_SERVER") or "microdata.worldbank.org"
    installation = ServerInstallation(hostname=resolved_host)
    return NadaServer(server=installation, api_key=api_key)


@app.command()
def installations(
    country: Annotated[
        str | None,
        typer.Option(
            "--country",
            "-c",
            help="Filter installations by 2-letter ISO country code (e.g. US, ID, ZA, UG) or country name.",
        ),
    ] = None,
    server: Annotated[
        str | None,
        typer.Option(
            "--server",
            "-s",
            help="Filter or search for a specific NADA server hostname.",
        ),
    ] = None,
    format: Annotated[OutputFormat, typer.Option("--format", "-f", help="Output format")] = OutputFormat.TABLE,
    limit: Annotated[int | None, typer.Option("--limit", "-l", help="Limit the number of results")] = None,
) -> None:
    """List known worldwide NADA installations."""
    all_installations = fetch_nada_installations(target_server=server, country=country)

    if limit:
        all_installations = all_installations[:limit]

    if format == OutputFormat.JSON:
        console.print_json(json.dumps([inst.model_dump(exclude_none=True) for inst in all_installations]))
        return

    if format == OutputFormat.CSV:
        writer = csv.DictWriter(
            sys.stdout,
            fieldnames=["name", "hostname", "country", "country_code", "version"],
        )
        writer.writeheader()
        for inst in all_installations:
            writer.writerow(
                {
                    "name": inst.name or "",
                    "hostname": inst.clean_hostname or "",
                    "country": inst.country or "",
                    "country_code": inst.country_code or "",
                    "version": str(inst.version or ""),
                }
            )
        return

    table = Table(title=f"Worldwide NADA Installations ({len(all_installations)} found)")
    table.add_column("Repository / Organization", style="cyan")
    table.add_column("Hostname", style="magenta")
    table.add_column("Country", style="green")
    table.add_column("Code", style="yellow", justify="center")
    table.add_column("Ver", style="dim", justify="center")

    for inst in all_installations:
        host = inst.clean_hostname or "N/A"
        clickable_host = f"[link={inst.url}]{host}[/link]" if inst.url else host
        table.add_row(
            inst.name or "N/A",
            clickable_host,
            inst.country or "N/A",
            inst.country_code or "-",
            str(inst.version or "-"),
        )

    console.print(table)


@app.command()
def info(
    hostname: Annotated[
        str | None,
        typer.Argument(
            help="NADA server hostname",
            envvar="NADA_SERVER",
        ),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option(
            "--api-key",
            "-k",
            envvar=["DARTFX_NADA_API_KEY", "NADA_API_KEY"],
            help="API Key / Token",
        ),
    ] = None,
) -> None:
    """Get information about a specific NADA server."""
    resolved_host = hostname or os.environ.get("NADA_SERVER") or "microdata.worldbank.org"
    server = get_server(resolved_host, api_key)

    with console.status(f"[bold green]Fetching info from {resolved_host}..."):
        try:
            ver = server.detect_server_version()
            collections = server.get_collections()
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")
            raise typer.Exit(code=1) from e

    console.print(f"\n[bold cyan]Server:[/] {server.installation.clean_hostname}")
    console.print(f"[bold cyan]URL:[/] {server.url}")
    console.print(f"[bold cyan]Detected Version:[/] v{ver}")

    coll_count = 0
    if isinstance(collections, list):
        coll_count = len(collections)
    elif isinstance(collections, dict) and "result" in collections:
        coll_count = len(collections["result"])
    console.print(f"[bold cyan]Available Collections:[/] {coll_count}")


@app.command()
def collections(
    hostname: Annotated[
        str | None,
        typer.Option(
            "--hostname",
            "-H",
            envvar="NADA_SERVER",
            help="NADA server hostname",
        ),
    ] = None,
    format: Annotated[OutputFormat, typer.Option("--format", "-f", help="Output format")] = OutputFormat.TABLE,
    api_key: Annotated[
        str | None,
        typer.Option(
            "--api-key",
            "-k",
            envvar=["DARTFX_NADA_API_KEY", "NADA_API_KEY"],
            help="API Key / Token",
        ),
    ] = None,
) -> None:
    """List collections in a NADA catalog."""
    resolved_host = hostname or os.environ.get("NADA_SERVER") or "microdata.worldbank.org"
    server = get_server(resolved_host, api_key)

    with console.status(f"[bold green]Fetching collections from {resolved_host}..."):
        try:
            result = server.get_collections()
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")
            raise typer.Exit(code=1) from e

    items = []
    if isinstance(result, list):
        items = result
    elif isinstance(result, dict) and "result" in result:
        items = result["result"]

    if format == OutputFormat.JSON:
        console.print_json(data=result)
        return

    if format == OutputFormat.CSV:
        writer = csv.DictWriter(sys.stdout, fieldnames=["repositoryid", "title"])
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    "repositoryid": item.get("repositoryid") or item.get("id", ""),
                    "title": item.get("title") or "",
                }
            )
        return

    table = Table(title=f"Collections on {resolved_host} ({len(items)} found)")
    table.add_column("Repository ID", style="cyan")
    table.add_column("Title", style="white")

    for item in items:
        rid = item.get("repositoryid") or str(item.get("id", "N/A"))
        title = item.get("title") or "N/A"
        table.add_row(rid, title)

    console.print(table)


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Search query for study metadata")] = "*",
    hostname: Annotated[
        str,
        typer.Option(
            "--hostname",
            "-H",
            envvar="NADA_SERVER",
            help="NADA server hostname",
        ),
    ] = "microdata.worldbank.org",
    country: Annotated[
        str | None,
        typer.Option("--country", "-c", help="Country name or ISO3 code"),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-l", help="Number of results to return")] = 25,
    sort: Annotated[
        Literal["rank", "title", "nation", "year"] | None,
        typer.Option("--sort", "-s", help="Sort field (rank, title, nation, year)"),
    ] = None,
    order: Annotated[
        Literal["asc", "desc"] | None,
        typer.Option("--order", "-o", help="Sort order (asc, desc)"),
    ] = None,
    format: Annotated[OutputFormat, typer.Option("--format", "-f", help="Output format")] = OutputFormat.TABLE,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", "-k", envvar=["DARTFX_NADA_API_KEY", "NADA_API_KEY"], help="API Key"),
    ] = None,
) -> None:
    """Search catalog studies."""
    server = get_server(hostname, api_key)
    params = SearchParameters(sk=query, country=country, ps=limit, sort_by=sort, sort_order=order)

    with console.status(f"[bold green]Searching {hostname} for '{query}'..."):
        try:
            results = server.search(params)
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")
            raise typer.Exit(code=1) from e

    if format == OutputFormat.JSON:
        console.print_json(data=results)
        return

    rows = []
    total = 0
    if isinstance(results, dict) and "result" in results:
        res = results["result"]
        rows = res.get("rows", [])
        total = res.get("total", len(rows))

    if format == OutputFormat.CSV:
        writer = csv.DictWriter(sys.stdout, fieldnames=["idno", "id", "title", "nation", "created"])
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "idno": r.get("idno", ""),
                    "id": str(r.get("id", "")),
                    "title": r.get("title", ""),
                    "nation": r.get("nation", ""),
                    "created": r.get("created", ""),
                }
            )
        return

    table = Table(title=f"Search Results for '{query}' on {hostname} (Total: {total})")
    table.add_column("Study IDNO", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("Nation / Country", style="green")
    table.add_column("Created", style="dim")

    for r in rows:
        idno = str(r.get("idno") or r.get("id", "N/A"))
        title = r.get("title") or "N/A"
        nation = r.get("nation") or "N/A"
        created = r.get("created") or "-"

        url = f"https://{server.installation.clean_hostname}/index.php/catalog/{idno}"
        idno_disp = f"[link={url}]{idno}[/link]"

        table.add_row(idno_disp, title, nation, created)

    console.print(table)


@app.command()
def study(
    identifier: Annotated[str, typer.Argument(help="Study IDNO or numeric identifier")],
    hostname: Annotated[
        str,
        typer.Option(
            "--hostname",
            "-H",
            envvar="NADA_SERVER",
            help="NADA server hostname",
        ),
    ] = "microdata.worldbank.org",
    export: Annotated[
        Literal["ddi", "json", "resources"] | None,
        typer.Option("--export", "-e", help="Export format (ddi, json, resources)"),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", "-k", envvar=["DARTFX_NADA_API_KEY", "NADA_API_KEY"], help="API Key"),
    ] = None,
) -> None:
    """Get metadata or download an export format (ddi, json, resources) for a study."""
    server = get_server(hostname, api_key)

    with console.status(f"[bold green]Fetching study '{identifier}' from {hostname}..."):
        try:
            if export == "ddi":
                result = server.get_study_ddi(identifier)
                console.print(result)
            elif export == "json":
                result = server.get_study_json(identifier)
                console.print_json(data=result)
            elif export == "resources":
                result = server.get_study_resources(identifier)
                console.print_json(data=result)
            else:
                result = server.get_study(identifier)
                console.print_json(data=result)
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")
            raise typer.Exit(code=1) from e


# Mount harvest command from harvester module
app.command(
    name="harvest",
    help="Harvest and incrementally sync NADA metadata records into local storage.",
)(_harvest_cmd)


@app.command(name="stats")
def stats(
    server: Annotated[
        str,
        typer.Option(
            "--server",
            "-s",
            envvar="NADA_SERVER",
            help="Target NADA server hostname or ALL.",
        ),
    ] = "ALL",
    country: Annotated[
        str | None,
        typer.Option(
            "--country",
            "-c",
            help="Filter servers by 2-letter ISO code or country name.",
        ),
    ] = None,
    query: Annotated[
        str | None,
        typer.Option(
            "--query",
            "-q",
            help="Filter studies by keyword query.",
        ),
    ] = None,
    api_token: Annotated[
        str | None,
        typer.Option(
            "--api-token",
            "-k",
            envvar=["DARTFX_NADA_API_KEY", "NADA_API_KEY"],
            help="NADA API Token for repositories requiring authentication.",
        ),
    ] = None,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            "-r",
            help="Force live statistics refresh, bypassing local 24-hour cache.",
        ),
    ] = False,
    cache_ttl: Annotated[
        int,
        typer.Option(
            "--cache-ttl",
            help="Statistics cache expiration in hours (default: 24).",
        ),
    ] = 24,
    timeout: Annotated[
        int,
        typer.Option(
            "--timeout",
            "-t",
            help="HTTP request timeout in seconds per server query (default: 10).",
        ),
    ] = 10,
) -> None:
    """Display live study count, collections, and latency for NADA servers."""
    from dartfx.nada.harvester import display_server_stats

    display_server_stats(
        server=server,
        country=country,
        query=query,
        api_token=api_token,
        refresh_cache=refresh,
        cache_ttl_hours=float(cache_ttl),
        timeout=timeout,
    )


@app.command(name="errors")
def errors(
    repo_dir: Annotated[
        str | None,
        typer.Argument(
            help="Path to repository storage root directory (defaults to DARTFX_NADA_REPOSITORY or ./).",
        ),
    ] = None,
    server: Annotated[
        str,
        typer.Option(
            "--server",
            "-s",
            envvar="NADA_SERVER",
            help="Filter error report by target server hostname (or ALL).",
        ),
    ] = "ALL",
    format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            "-f",
            help="Output format: table (default), json, or csv.",
        ),
    ] = OutputFormat.TABLE,
    by_format: Annotated[
        bool,
        typer.Option(
            "--by-format",
            help="Breakdown error counts in a matrix by metadata format.",
        ),
    ] = False,
    by_server: Annotated[
        bool,
        typer.Option(
            "--by-server",
            help="Breakdown error counts by server repository.",
        ),
    ] = False,
    details: Annotated[
        bool,
        typer.Option(
            "--details",
            "-d",
            help="Show individual failed study IDNOs and reasons.",
        ),
    ] = False,
) -> None:
    """Analyze and report counts per harvest error type from repository manifests."""
    from dartfx.nada.harvester import analyze_harvest_errors, render_harvest_errors

    analysis = analyze_harvest_errors(repo_dir=repo_dir, server=server)

    if format == OutputFormat.JSON:
        console.print_json(json.dumps(analysis, indent=2))
        return

    if format == OutputFormat.CSV:
        writer = csv.writer(sys.stdout)
        if details:
            writer.writerow(["server", "pid", "url", "format", "error_type", "failed_at", "reason"])
            for rec in analysis.get("records", []):
                writer.writerow(
                    [
                        rec.get("server", ""),
                        rec.get("pid", ""),
                        rec.get("url", ""),
                        rec.get("format", ""),
                        rec.get("error_type", ""),
                        rec.get("failed_at", ""),
                        rec.get("reason", ""),
                    ]
                )
        elif by_server:
            writer.writerow(["server", "error_count", "percentage"])
            tot = analysis.get("total_errors", 1)
            for srv, cnt in analysis.get("by_server", {}).items():
                writer.writerow([srv, cnt, f"{(cnt / tot) * 100:.1f}%"])
        else:
            writer.writerow(["error_type", "count", "percentage"])
            tot = analysis.get("total_errors", 1)
            for etype, cnt in analysis.get("by_type", {}).items():
                writer.writerow([etype, cnt, f"{(cnt / tot) * 100:.1f}%"])
        return

    render_harvest_errors(
        analysis=analysis,
        by_format=by_format,
        by_server=by_server,
        details=details,
        console_out=console,
    )


if __name__ == "__main__":
    app()
