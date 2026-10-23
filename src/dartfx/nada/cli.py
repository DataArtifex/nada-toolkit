import csv
import json
import os
import sys
from enum import StrEnum
from typing import Annotated, Any, Literal

import typer
from dotenv import find_dotenv, load_dotenv
from rich import box
from rich.console import Console
from rich.table import Table

from dartfx.nada.__about__ import __version__
from dartfx.nada.harvester import harvest as _harvest_cmd
from dartfx.nada.nada import (
    NadaServer,
    SearchParameters,
    fetch_nada_installations,
)

load_dotenv(find_dotenv(usecwd=True))


def _normalize_smart_dashes() -> None:
    """Sanitize macOS smart dashes and bare -o/--output flags in sys.argv."""
    normalized: list[str] = []
    for arg in sys.argv:
        if any(arg.startswith(prefix) for prefix in ("-–", "—", "–", "−")):
            clean = arg.replace("-–", "--").replace("—", "--").replace("−", "-")
            if clean.startswith("–"):
                clean = "--" + clean[1:] if len(clean) > 2 else "-" + clean[1:]
            normalized.append(clean)
        else:
            normalized.append(arg)

    final_args: list[str] = []
    i = 0
    while i < len(normalized):
        arg = normalized[i]
        if arg in ("-o", "--output"):
            if i + 1 < len(normalized) and not (normalized[i + 1].startswith("-") and normalized[i + 1] != "-"):
                final_args.append(arg)
                final_args.append(normalized[i + 1])
                i += 2
                continue
            else:
                final_args.append("--output=AUTO")
                i += 1
                continue
        final_args.append(arg)
        i += 1

    sys.argv[:] = final_args


_normalize_smart_dashes()

app = typer.Typer(
    name="dartfx-nada",
    help="CLI for discovering and interacting with IHSN NADA repositories.",
    add_completion=False,
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"dartfx-nada {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            "-V",
            help="Show the version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = None,
) -> None:
    """CLI for discovering and interacting with IHSN NADA repositories."""


class OutputFormat(StrEnum):
    TABLE = "table"
    JSON = "json"
    CSV = "csv"
    MARKDOWN = "markdown"
    MD = "md"


def get_server(hostname: str | None = None, api_key: str | None = None) -> NadaServer:
    """Helper to create a NadaServer instance."""
    resolved_host = hostname or os.environ.get("NADA_SERVER") or "microdata.worldbank.org"
    return NadaServer(server=resolved_host, api_key=api_key)


@app.command(name="list")
@app.command(name="installations", help="Alias for 'list'. List known worldwide NADA installations.")
def list_installations(
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
    if format in (OutputFormat.MARKDOWN, OutputFormat.MD):
        md_lines = [
            "| Repository / Organization | Hostname | Country | Code | Ver |",
            "|:---|:---|:---|:---:|:---:|",
        ]
        for inst in all_installations:
            name_val = (inst.name or "").replace("|", "\\|")
            host_val = inst.clean_hostname or ""
            url_val = inst.catalog_url or inst.url
            host_link = f"[{host_val}]({url_val})" if url_val else host_val
            c_name = inst.country or "-"
            c_code = inst.country_code or "-"
            ver_str = inst.version or "-"
            md_lines.append(f"| {name_val} | {host_link} | {c_name} | {c_code} | {ver_str} |")
        print("\n".join(md_lines))
        return

    table = Table(title=f"Worldwide NADA Installations ({len(all_installations)} found)")
    table.add_column("Repository / Organization", style="cyan")
    table.add_column("Hostname", style="magenta")
    table.add_column("Country", style="green")
    table.add_column("Code", style="yellow", justify="center")
    table.add_column("Ver", style="dim", justify="center")

    for inst in all_installations:
        host = inst.clean_hostname or "N/A"
        link_url = inst.catalog_url or inst.url
        clickable_host = f"[link={link_url}]{host}[/link]" if link_url else host
        table.add_row(
            inst.name or "N/A",
            clickable_host,
            inst.country or "N/A",
            inst.country_code or "-",
            str(inst.version or "-"),
        )

    console.print(table)


# Backward compatibility alias
installations = list_installations


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
    """Get rich information, catalog metrics, and export formats for a NADA server."""
    resolved_host = hostname or os.environ.get("NADA_SERVER") or "microdata.worldbank.org"
    server = get_server(resolved_host, api_key)

    with console.status(f"[bold green]Fetching info from {server.installation.clean_hostname}..."):
        try:
            ver = server.detect_server_version()
            collections = server.get_collections()
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")
            raise typer.Exit(code=1) from e

        # Query total studies count and protocol health
        from dartfx.nada.harvester import fetch_server_stats, format_php_version

        studies_count: int | None = None
        st: dict[str, Any] = {}
        try:
            if ver == 5:
                params = SearchParameters(ps=1)
                search_res = server.search(params)
                if isinstance(search_res, dict):
                    res = search_res.get("result", {})
                    if isinstance(res, dict):
                        try:
                            studies_count = int(res.get("total", len(res.get("rows", []))))
                        except (ValueError, TypeError):
                            studies_count = len(res.get("rows", []))
                    elif isinstance(res, list):
                        studies_count = len(res)

            host_to_probe = server.url if server.installation.scheme == "http" else server.installation.clean_hostname
            st = fetch_server_stats(host_to_probe, api_token=api_key)
            if studies_count is None and st.get("studies"):
                studies_count = st.get("studies")
        except Exception:
            pass

    coll_count = 0
    if isinstance(collections, list):
        coll_count = len(collections)
    elif isinstance(collections, dict) and "result" in collections:
        res = collections["result"]
        coll_count = len(res) if isinstance(res, (list, dict)) else 0
    elif isinstance(collections, dict) and "collections" in collections:
        res = collections["collections"]
        coll_count = len(res) if isinstance(res, list) else 0

    table = Table(
        title=f"NADA Server: {server.installation.clean_hostname}",
        box=box.ROUNDED,
        title_style="bold cyan",
        show_header=False,
    )
    table.add_column("Property", style="bold white", width=24)
    table.add_column("Value", style="cyan")

    if server.installation.name and server.installation.name != server.installation.clean_hostname:
        table.add_row("Organization / Name", server.installation.name)
    table.add_row("Server Hostname", server.installation.clean_hostname)
    catalog_endpoint = server.installation.catalog_url or server.url
    table.add_row("Catalog Base URL", f"[link={catalog_endpoint}]{catalog_endpoint}[/link]")
    table.add_row("Detected Version", f"v{ver}")
    table.add_row(
        "Total Studies",
        f"{studies_count:,}" if studies_count is not None else "[dim]Unknown / Unreachable[/dim]",
    )
    table.add_row("Available Collections", f"{coll_count:,}" if coll_count > 0 else "0 (None or Not Exposed)")
    if server.installation.country:
        country_display = server.installation.country
        if server.installation.country_code:
            country_display += f" ({server.installation.country_code})"
        table.add_row("Country", country_display)

    if st.get("php_version"):
        table.add_row(
            "PHP Runtime",
            format_php_version(st.get("php_version"), nada_ver=ver, is_online=True),
        )
    if st.get("web_server"):
        table.add_row("Web Server", str(st.get("web_server")))

    if st.get("http_fallback"):
        table.add_row(
            "Protocol Status",
            f"[yellow]HTTP Fallback (HTTPS SSL Failed: {st.get('ssl_error', 'SSL Failed')})[/yellow]",
        )
        table.add_row(
            "Protocol Advisory",
            "[dim yellow]Notice: Reached via HTTP fallback because HTTPS failed SSL verification.[/dim yellow]",
        )
    elif st.get("https_upgrade_recommended"):
        adv_msg = (
            "[bold green]HTTPS is available and functional! Recommend switching URL "
            f"from HTTP to https://{server.installation.clean_hostname}[/bold green]"
        )
        table.add_row("Protocol Advisory", adv_msg)

    console.print()
    console.print(table)

    fmt_table = Table(
        title="Supported Metadata & Export Formats",
        box=box.ROUNDED,
        title_style="bold green",
        header_style="bold yellow",
    )
    fmt_table.add_column("Format", style="bold cyan", width=22)
    fmt_table.add_column("Flag / Key", style="magenta", width=12)
    fmt_table.add_column("API Route", style="dim white", width=30)
    fmt_table.add_column("Description", style="white")

    fmt_table.add_row(
        "DDI 2.5 XML",
        "ddi",
        "/catalog/ddi/{id}",
        "Data Documentation Initiative CodeBook 2.5 XML specification",
    )
    fmt_table.add_row(
        "Study JSON",
        "json",
        "/catalog/json/{id}",
        "Comprehensive study metadata, sampling, and variable JSON",
    )
    fmt_table.add_row(
        "External Resources",
        "resources",
        "/catalog/resources/{id}",
        "Questionnaires, technical reports, and survey documentation",
    )
    fmt_table.add_row(
        "Catalog CSV Export",
        "csv",
        "/catalog/export/csv",
        "Bulk tabular catalog index export (IDNO, Title, Nation)",
    )
    fmt_table.add_row(
        "MLCommons Croissant",
        "croissant",
        "/catalog/croissant/{id}",
        "AI/ML-ready JSON-LD dataset format (World Bank Metadata Editor standard)",
    )
    fmt_table.add_row(
        "Schema.org Dataset",
        "schema",
        "/catalog/{id}",
        "Google Dataset Search JSON-LD markup embedded in study landing pages",
    )

    console.print(fmt_table)


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
        try:
            total = int(res.get("total", len(rows)))
        except (ValueError, TypeError):
            total = len(rows)

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
        Literal["ddi", "json", "resources", "croissant", "schema"] | None,
        typer.Option("--export", "-e", help="Export format (ddi, json, resources, croissant, schema)"),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", "-k", envvar=["DARTFX_NADA_API_KEY", "NADA_API_KEY"], help="API Key"),
    ] = None,
) -> None:
    """Get metadata or download an export format (ddi, json, resources, croissant, schema) for a study."""
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
            elif export == "croissant":
                result = server.get_study_croissant(identifier)
                console.print_json(data=result)
            elif export == "schema":
                result = server.get_study_schema(identifier)
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
    show_env: Annotated[
        bool,
        typer.Option(
            "--show-env",
            "-e",
            help="Display extended server environment details (web server banner).",
        ),
    ] = False,
    sort: Annotated[
        str,
        typer.Option(
            "--sort",
            "-S",
            help="Sort results by field: name (default), country, studies, latency, php, status.",
        ),
    ] = "name",
    desc: Annotated[
        bool,
        typer.Option(
            "--desc",
            help="Sort in descending order (default for studies).",
        ),
    ] = False,
    asc: Annotated[
        bool,
        typer.Option(
            "--asc",
            help="Force ascending sort order.",
        ),
    ] = False,
    format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            "-f",
            help="Output format: table (default), json, markdown, or csv.",
        ),
    ] = OutputFormat.TABLE,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output results as JSON (shorthand for --format json).",
        ),
    ] = False,
    markdown_output: Annotated[
        bool,
        typer.Option(
            "--markdown",
            "--md",
            help="Output results as Markdown table (shorthand for --format markdown).",
        ),
    ] = False,
    output: Annotated[
        str | None,
        typer.Option(
            "--output",
            "-o",
            help="Write output to a file instead of console (default: dartfx-nada.stats.txt|json|md|csv).",
        ),
    ] = None,
) -> None:
    """Display live study count, latency, and status for NADA servers."""
    from dartfx.nada.harvester import display_server_stats

    descending: bool | None = None
    if asc:
        descending = False
    elif desc:
        descending = True

    if json_output:
        effective_format = OutputFormat.JSON
    elif markdown_output:
        effective_format = OutputFormat.MARKDOWN
    else:
        effective_format = format

    display_server_stats(
        server=server,
        country=country,
        query=query,
        api_token=api_token,
        refresh_cache=refresh,
        cache_ttl_hours=float(cache_ttl),
        timeout=timeout,
        show_env=show_env,
        sort_by=sort,
        descending=descending,
        format=effective_format.value,
        output=output,
    )


@app.command(name="errors")
def errors(
    repo_dir: Annotated[
        str | None,
        typer.Argument(
            help="Path to repository storage root directory (defaults to DARTFX_NADA_REPOSITORY or ./repository).",
        ),
    ] = None,
    repo_dir_opt: Annotated[
        str | None,
        typer.Option(
            "--repo-dir",
            "-r",
            envvar="DARTFX_NADA_REPOSITORY",
            help="Path to repository storage root directory.",
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

    effective_repo_dir = repo_dir or repo_dir_opt
    analysis = analyze_harvest_errors(repo_dir=effective_repo_dir, server=server)

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


@app.command(name="registry-md", help="Generate or check sidecar data/servers.md from data/servers.yaml.")
def registry_md_command(
    yaml_file: Annotated[
        str | None,
        typer.Option("--yaml", "-y", help="Path to servers.yaml (default: auto-detected data/servers.yaml)"),
    ] = None,
    output_file: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Path to servers.md (default: sidecar data/servers.md)"),
    ] = None,
    check: Annotated[
        bool,
        typer.Option("--check", help="Check whether servers.md is in sync with servers.yaml without modifying"),
    ] = False,
) -> None:
    """Generate or verify the sidecar Markdown version of the servers registry."""
    from dartfx.nada.markdown import check_servers_markdown, generate_servers_markdown

    if check:
        in_sync = check_servers_markdown(yaml_file, output_file)
        if in_sync:
            console.print("[green]✓ data/servers.md is up to date.[/green]")
            return
        console.print("[red]✗ Drift detected: data/servers.md is out of sync with data/servers.yaml.[/red]")
        console.print("Run [bold cyan]dartfx-nada registry-md[/bold cyan] to update the sidecar markdown.")
        raise typer.Exit(code=1)

    dest_md, content = generate_servers_markdown(yaml_file, output_file)
    console.print(
        f"[green]✓ Successfully generated sidecar registry markdown:[/green] "
        f"[cyan]{dest_md}[/cyan] ({len(content)} bytes)"
    )


if __name__ == "__main__":
    app()
