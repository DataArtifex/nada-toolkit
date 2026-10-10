#!/usr/bin/env python3
"""
NADA Metadata Harvester & Sync Engine

A smart, incremental harvester for NADA DDI CodeBook and survey metadata records.
Uses Typer for CLI interface and Rich for terminal visualizations.
"""

import hashlib
import json
import os
import re
import time
import urllib.parse
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import requests
import typer
from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

from .__about__ import __version__
from .nada import (
    ServerInstallation,
    fetch_nada_installations,
)

os.environ["PYTHONWARNINGS"] = "ignore"
warnings.filterwarnings("ignore")

app = typer.Typer(
    name="nada-harvest",
    help="Harvest and incrementally sync NADA metadata records into local storage.",
    add_completion=False,
)

load_dotenv(find_dotenv(usecwd=True))
console = Console()


class HarvesterFileLogger:
    """File logger for recording execution requests and verbose activity to a timestamped log file."""

    def __init__(self, log_path: Path | None = None):
        self.log_path = log_path
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, message: str, level: str = "INFO") -> None:
        try:
            plain_msg = Text.from_markup(message).plain
        except Exception:
            plain_msg = message

        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        formatted = f"{timestamp} [{level}] {plain_msg}"

        if self.log_path:
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(formatted + "\n")
            except Exception:
                pass


file_logger = HarvesterFileLogger()


def sanitize_pid(identifier: str) -> str:
    """Convert study IDNO or PID to a safe directory name."""
    return re.sub(r"[^a-zA-Z0-9_\-\.]", "_", identifier)


def format_version(version: str | int | float | None) -> str:
    """Format a NADA version string into a clean semantic version (e.g. 'v5', 'v4', 'v5.2')."""
    if not version:
        return "-"
    ver_str = str(version).strip()
    if not ver_str or ver_str == "-":
        return "-"
    match = re.search(r"v?(\d+(?:\.\d+)*)", ver_str, re.IGNORECASE)
    if match:
        return f"v{match.group(1)}"
    return ver_str


def get_dataset_metadata_path(server_dir: Path, pid: str, ext: str = ".ddi.xml") -> tuple[Path, str]:
    """Get study directory and metadata file path inside a dedicated metadata/ folder.
    Returns (full_file_path, relative_path_str).
    """
    pid_dir_name = sanitize_pid(pid)
    metadata_dir = server_dir / pid_dir_name / "metadata"
    file_name = ext.lstrip(".")
    rel_path = f"{pid_dir_name}/metadata/{file_name}"
    return metadata_dir / file_name, rel_path


FORMAT_EXTENSIONS: dict[str, str] = {
    "ddi": ".ddi.xml",
    "ddi-c": ".ddi.xml",
    "xml": ".ddi.xml",
    "json": ".nada.json",
    "metadata": ".nada.json",
    "resources": ".resources.json",
}

ALL_SUPPORTED_FORMATS: list[str] = ["ddi", "json", "resources"]


def get_format_extension(fmt: str) -> str:
    """Get the standard file extension for a given metadata format."""
    clean_fmt = fmt.strip().lower()
    return FORMAT_EXTENSIONS.get(clean_fmt, f".{clean_fmt}")


def normalize_formats(formats_input: str | list[str] | None) -> list[str]:
    """Parse comma-separated or list of metadata formats into a clean list."""
    if not formats_input:
        return []
    if isinstance(formats_input, str):
        raw_list = [formats_input]
    else:
        raw_list = list(formats_input)

    parsed = []
    for entry in raw_list:
        for fmt in entry.split(","):
            fmt_clean = fmt.strip().lower()
            if not fmt_clean:
                continue
            if fmt_clean == "all":
                return list(ALL_SUPPORTED_FORMATS)
            if fmt_clean in ("xml", "ddi-c"):
                fmt_clean = "ddi"
            elif fmt_clean == "metadata":
                fmt_clean = "json"
            if fmt_clean in ALL_SUPPORTED_FORMATS and fmt_clean not in parsed:
                parsed.append(fmt_clean)
    return parsed


DEFAULT_REQUEST_HEADERS: dict[str, str] = {
    "User-Agent": f"dartfx-nada/{__version__} (Research Harvester; +https://github.com/DataArtifex/nada-toolkit)",
    "Accept": "application/json, text/plain, */*",
}


def resolve_server_token(
    host: str | None = None,
    repo_root: Path | None = None,
    explicit_token: str | None = None,
) -> str | None:
    """Resolve API token for a NADA server by searching in order:
    1. Explicitly supplied CLI token (--api-token / -k)
    2. Server-specific directory token file (<repo_root>/<host>/.api_token)
    3. Central repository root tokens mapping (<repo_root>/.nada_tokens.json)
    4. Current working directory tokens (.nada_tokens.json or .tokens.json)
    5. Server-specific environment variable (e.g. NADA_API_TOKEN_MICRODATA_WORLDBANK_ORG)
    6. Global environment variable (NADA_API_KEY, DARTFX_NADA_API_KEY, NADA_API_TOKEN)
    """
    if explicit_token and explicit_token.strip():
        return explicit_token.strip()

    if not host:
        return (
            os.environ.get("NADA_API_KEY") or os.environ.get("DARTFX_NADA_API_KEY") or os.environ.get("NADA_API_TOKEN")
        )

    clean_host = host.replace("https://", "").replace("http://", "").strip("/")
    if "/" in clean_host:
        clean_host = clean_host.split("/", 1)[0]

    # 1. Check server-specific directory (.api_token)
    search_dirs = []
    if repo_root:
        search_dirs.append(repo_root)
    env_repo = os.environ.get("DARTFX_NADA_REPOSITORY")
    if env_repo:
        search_dirs.append(Path(env_repo))
    search_dirs.append(Path.cwd())

    for d in search_dirs:
        if d:
            stf = Path(d) / clean_host / ".api_token"
            if stf.exists():
                try:
                    t = stf.read_text("utf-8").strip()
                    if t:
                        return t
                except Exception:
                    pass

    # 2. Check central .nada_tokens.json / .tokens.json
    for d in search_dirs:
        if d:
            for token_filename in [".nada_tokens.json", ".tokens.json"]:
                ctf = Path(d) / token_filename
                if ctf.exists():
                    try:
                        m = json.loads(ctf.read_text("utf-8"))
                        if isinstance(m, dict):
                            for k, v in m.items():
                                k_clean = k.replace("https://", "").replace("http://", "").strip("/")
                                if "/" in k_clean:
                                    k_clean = k_clean.split("/", 1)[0]
                                if k_clean.lower() == clean_host.lower() and v:
                                    return str(v).strip()
                    except Exception:
                        pass

    # 3. Server-specific environment variable
    clean_env_name = re.sub(r"[^A-Za-z0-9]", "_", clean_host).upper()
    env_server_key = f"NADA_API_TOKEN_{clean_env_name}"
    if os.environ.get(env_server_key):
        return os.environ[env_server_key].strip()

    # 4. Global environment variable
    if os.environ.get("NADA_API_KEY"):
        return os.environ["NADA_API_KEY"].strip()
    if os.environ.get("DARTFX_NADA_API_KEY"):
        return os.environ["DARTFX_NADA_API_KEY"].strip()
    if os.environ.get("NADA_API_TOKEN"):
        return os.environ["NADA_API_TOKEN"].strip()

    return None


def save_server_token(host: str, token: str, repo_root: Path | None = None) -> None:
    """Save an API token for a server to both its server directory (.api_token) and central .nada_tokens.json."""
    clean_host = host.replace("https://", "").replace("http://", "").strip("/")
    if "/" in clean_host:
        clean_host = clean_host.split("/", 1)[0]
    clean_token = token.strip()
    if not clean_token:
        return

    # 1. Save to central .nada_tokens.json in repo_root (or current dir)
    central_dir = repo_root if repo_root else Path.cwd()
    central_file = central_dir / ".nada_tokens.json"
    tokens_map = {}
    if central_file.exists():
        try:
            tokens_map = json.loads(central_file.read_text("utf-8"))
        except Exception:
            tokens_map = {}
    tokens_map[clean_host] = clean_token
    try:
        central_file.write_text(json.dumps(tokens_map, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass

    # 2. Save to server-specific directory (.api_token) if server_dir exists
    if repo_root:
        server_dir = repo_root / clean_host
        if server_dir.exists():
            token_file = server_dir / ".api_token"
            try:
                token_file.write_text(clean_token + "\n", encoding="utf-8")
            except Exception:
                pass


def get_request_headers(
    host: str | None = None,
    api_token: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, str]:
    """Return standard headers with User-Agent and resolved NADA API key."""
    headers = dict(DEFAULT_REQUEST_HEADERS)
    resolved_token = resolve_server_token(host, repo_root=repo_root, explicit_token=api_token)
    if resolved_token:
        headers["X-API-KEY"] = resolved_token.strip()
    return headers


def format_dataset_url(pid: str, host: str | None = None) -> str:
    """Format study URL for console linking."""
    clean_host = host.replace("https://", "").replace("http://", "").strip("/") if host else ""
    if "/" in clean_host:
        clean_host = clean_host.split("/", 1)[0]
    if clean_host:
        return f"https://{clean_host}/index.php/catalog/{pid}"
    return f"https://nada.ihsn.org/catalog/{pid}"


def is_non_recoverable_error(reason: str | None) -> bool:
    """Check if an error reason is permanent/non-recoverable (no need to retry)."""
    if not reason:
        return False
    r_low = reason.lower()
    return any(
        k in r_low
        for k in [
            "not supported",
            "unsupported format",
            "http 404",
            "http 400",
            "http 403",
            "http 410",
            "empty metadata response",
            "deaccessioned",
        ]
    )


def _extract_http_error_reason(r: requests.Response) -> str:
    """Extract informative error reason from HTTP response."""
    try:
        data = r.json()
        if isinstance(data, dict):
            if "message" in data and data["message"]:
                return f"HTTP {r.status_code}: {data['message']}"
            if "error" in data and data["error"]:
                return f"HTTP {r.status_code}: {data['error']}"
    except Exception:
        pass
    if r.status_code == 403:
        return "HTTP 403: Forbidden"
    if r.status_code == 404:
        return "HTTP 404: Not Found"
    return f"HTTP {r.status_code}"


def classify_harvest_error(reason: str | None) -> str:
    """Categorize an error message string into a standardized, descriptive error category."""
    if not reason:
        return "Unknown Error"
    r_low = reason.lower()

    # 1. Format Unsupported
    if any(k in r_low for k in ["not supported", "unsupported format", "exporter not found"]):
        return "Exporter Not Supported on Server"

    # 2. Authentication Required
    if "401" in r_low or "unauthorized" in r_low or "api key required" in r_low:
        return "HTTP 401: Authentication Required (API Token)"

    # 3. Restricted Study / Forbidden
    if any(k in r_low for k in ["restricted", "permission denied", "requires authentication"]):
        return "HTTP 403: Restricted Study (Authentication Required)"

    # 4. Bot Protection / WAF
    if any(k in r_low for k in ["cloudflare", "bot protection", "waf", "captcha", "ddos-guard", "incapsula"]):
        return "HTTP 403: Forbidden / Bot Protection (WAF)"

    if "403" in r_low or "forbidden" in r_low:
        return "HTTP 403: Forbidden"

    # 5. Not Found / Deaccessioned
    if "404" in r_low or "not found" in r_low:
        return "HTTP 404: Study / Endpoint Not Found"
    if "410" in r_low or "deaccessioned" in r_low or "gone" in r_low:
        return "HTTP 410: Study Deaccessioned"

    # 6. Unprocessable
    if "422" in r_low or "unprocessable" in r_low:
        return "HTTP 422: Unprocessable Entity"

    # 7. Server Errors
    if any(
        k in r_low
        for k in [
            "500",
            "502",
            "503",
            "504",
            "internal server error",
            "bad gateway",
            "service unavailable",
        ]
    ):
        return "HTTP 5xx: Server / Upstream Error"

    # 8. Network Errors
    if any(k in r_low for k in ["timeout", "timed out", "connecttimeout", "readtimeout"]):
        return "Network: Request Timeout"

    if any(
        k in r_low
        for k in [
            "connection refused",
            "connection error",
            "max retries exceeded",
            "failed to establish a new connection",
        ]
    ):
        return "Network: Connection Failure"

    # 9. Parse Errors
    if any(
        k in r_low
        for k in [
            "parseerror",
            "jsondecodeerror",
            "syntaxerror",
            "xml.etree",
            "invalid xml",
            "invalid json",
            "empty metadata response",
        ]
    ):
        return "Parse Error: Malformed XML / JSON"

    # 10. SSL / TLS Diagnostics
    if "ssl" in r_low or "certificate" in r_low:
        if (
            "unable to get local issuer certificate" in r_low
            or "unable to verify the first certificate" in r_low
            or "missing intermediate" in r_low
            or "incomplete chain" in r_low
        ):
            return "Network: SSL Incomplete Chain (Missing Intermediate CA)"
        if "expired" in r_low:
            return "Network: SSL Certificate Expired"
        if "hostname" in r_low and ("mismatch" in r_low or "doesn't match" in r_low):
            return "Network: SSL Hostname Mismatch"
        if "self-signed" in r_low or "self signed" in r_low:
            return "Network: SSL Self-Signed Certificate"
        return "Network: SSL / TLS Certificate Error"

    return "Other Error"


def analyze_harvest_errors(
    repo_dir: Path | str | None = None,
    server: str | None = None,
) -> dict[str, Any]:
    """Scan repository storage directories for .manifest.json files and aggregate error statistics."""
    if repo_dir is None:
        env_dir = os.environ.get("DARTFX_NADA_REPOSITORY")
        base_path = Path(env_dir).resolve() if env_dir else Path.cwd()
    else:
        base_path = Path(repo_dir).resolve()

    manifest_files: list[Path] = []
    if base_path.is_file() and base_path.name == ".manifest.json":
        manifest_files = [base_path]
    elif base_path.is_dir():
        if (base_path / ".manifest.json").exists():
            manifest_files = [base_path / ".manifest.json"]
        else:
            manifest_files = sorted(base_path.glob("*/.manifest.json"))
            if not manifest_files:
                manifest_files = sorted(base_path.rglob(".manifest.json"))

    by_type: dict[str, int] = {}
    by_format: dict[str, int] = {}
    by_server: dict[str, int] = {}
    matrix: dict[str, dict[str, int]] = {}
    records: list[dict[str, Any]] = []
    affected_datasets: set[str] = set()

    clean_server = (
        server.replace("https://", "").replace("http://", "").strip("/") if server and server.upper() != "ALL" else None
    )

    for mfile in manifest_files:
        try:
            with open(mfile, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        srv = data.get("server") or mfile.parent.name
        if clean_server and srv.lower() != clean_server.lower():
            continue

        errors_map = data.get("errors", {})
        for rec_key, err_info in errors_map.items():
            pid = err_info.get("pid") or err_info.get("global_id") or rec_key.split("::")[0]
            fmt = err_info.get("format") or (rec_key.split("::")[1] if "::" in rec_key else "unknown")
            raw_reason = err_info.get("reason", "")
            err_cat = classify_harvest_error(raw_reason)

            by_type[err_cat] = by_type.get(err_cat, 0) + 1
            by_format[fmt] = by_format.get(fmt, 0) + 1
            by_server[srv] = by_server.get(srv, 0) + 1

            if err_cat not in matrix:
                matrix[err_cat] = {}
            matrix[err_cat][fmt] = matrix[err_cat].get(fmt, 0) + 1

            affected_datasets.add(f"{srv}::{pid}")
            records.append(
                {
                    "server": srv,
                    "pid": pid,
                    "format": fmt,
                    "error_type": err_cat,
                    "reason": raw_reason,
                    "failed_at": err_info.get("failed_at", ""),
                    "url": err_info.get("url") or format_dataset_url(pid, host=srv),
                }
            )

    sorted_by_type = dict(sorted(by_type.items(), key=lambda x: x[1], reverse=True))
    sorted_by_server = dict(sorted(by_server.items(), key=lambda x: x[1], reverse=True))

    return {
        "total_errors": len(records),
        "total_datasets": len(affected_datasets),
        "servers_with_errors": len(by_server),
        "by_type": sorted_by_type,
        "by_format": by_format,
        "by_server": sorted_by_server,
        "matrix": matrix,
        "records": records,
    }


def render_harvest_errors(
    analysis: dict[str, Any],
    by_format: bool = False,
    by_server: bool = False,
    details: bool = False,
    console_out: Console | None = None,
) -> None:
    """Render the structured error analysis to the terminal."""
    out = console_out or console
    total = analysis.get("total_errors", 0)

    if total == 0:
        out.print(Panel("[bold green]✓ No harvest errors recorded in repository manifests.[/bold green]", expand=False))
        return

    out.print(
        Panel(
            f"[bold red]Total Harvest Errors:[/] {total:,}  |  "
            f"[bold yellow]Affected Studies:[/] {analysis.get('total_datasets', 0):,}  |  "
            f"[bold cyan]Servers Affected:[/] {analysis.get('servers_with_errors', 0):,}",
            title="[bold red]NADA Harvest Error Analysis[/bold red]",
            expand=False,
        )
    )

    if by_format:
        all_fmts = sorted(analysis.get("by_format", {}).keys())
        matrix_table = Table(
            title="Harvest Errors: Breakdown by Error Type & Format",
            header_style="bold magenta",
            show_header=True,
        )
        matrix_table.add_column("Error Category / Type", style="cyan", no_wrap=False)
        for fmt_name in all_fmts:
            matrix_table.add_column(fmt_name.capitalize(), justify="right", style="yellow")
        matrix_table.add_column("Total", justify="right", style="bold red")

        matrix = analysis.get("matrix", {})
        for err_type, count in analysis.get("by_type", {}).items():
            row_cells = [err_type]
            for fmt_name in all_fmts:
                c = matrix.get(err_type, {}).get(fmt_name, 0)
                row_cells.append(str(c) if c > 0 else "[dim]-[/dim]")
            row_cells.append(str(count))
            matrix_table.add_row(*row_cells)

        out.print(matrix_table)

    elif by_server:
        srv_table = Table(
            title="Harvest Errors: Breakdown by Server",
            header_style="bold cyan",
            show_header=True,
        )
        srv_table.add_column("Server Hostname", style="cyan")
        srv_table.add_column("Error Count", justify="right", style="bold red")
        srv_table.add_column("% of Total", justify="right", style="yellow")

        for srv, count in analysis.get("by_server", {}).items():
            pct = f"{(count / total) * 100:.1f}%" if total > 0 else "0%"
            srv_table.add_row(srv, str(count), pct)

        out.print(srv_table)

    else:
        type_table = Table(
            title="Harvest Errors: Count per Error Type",
            header_style="bold red",
            show_header=True,
        )
        type_table.add_column("Error Category / Type", style="cyan", no_wrap=False)
        type_table.add_column("Count", justify="right", style="bold red")
        type_table.add_column("% of Total", justify="right", style="yellow")

        for err_type, count in analysis.get("by_type", {}).items():
            pct = f"{(count / total) * 100:.1f}%" if total > 0 else "0%"
            type_table.add_row(err_type, str(count), pct)

        out.print(type_table)

    if details:
        records = analysis.get("records", [])
        rec_table = Table(
            title=f"Detailed Error Records ({len(records)} Items)",
            header_style="bold yellow",
            show_header=True,
        )
        rec_table.add_column("Server", style="dim", no_wrap=True)
        rec_table.add_column("Study IDNO", style="cyan", no_wrap=True)
        rec_table.add_column("Format", style="yellow", justify="center", no_wrap=True)
        rec_table.add_column("Error Category", style="magenta")
        rec_table.add_column("Reason / Snippet", style="red", no_wrap=False)

        for rec in records:
            reason = rec.get("reason", "")
            if len(reason) > 90:
                reason = reason[:87] + "..."
            srv = rec.get("server", "-")
            pid = rec.get("pid", "-")
            url = rec.get("url") or format_dataset_url(pid, host=srv)

            clean_srv = srv.replace("https://", "").replace("http://", "").strip("/") if srv else ""
            srv_display = f"[link=https://{clean_srv}]{clean_srv}[/link]" if clean_srv and clean_srv != "-" else srv
            pid_display = f"[link={url}]{pid}[/link]" if url and pid != "-" else pid

            rec_table.add_row(
                srv_display,
                pid_display,
                rec.get("format", "-"),
                rec.get("error_type", "-"),
                reason,
            )

        out.print(rec_table)


def format_response_latency(
    latency: float | int | None,
    is_online: bool = True,
    requires_token: bool = False,
    is_error: bool = False,
) -> str:
    """Format response latency with a color-coded performance or status badge."""
    if latency is None:
        return "-"
    lat = float(latency)
    if is_error or not is_online:
        return f"[dim red]❌ {lat:.1f}s[/dim red]"
    if requires_token:
        return f"[yellow]🔒 {lat:.1f}s[/yellow]"

    if lat < 3.0:
        return f"[bold green]⚡ {lat:.1f}s[/bold green]"
    elif lat <= 8.0:
        return f"[green]🟢 {lat:.1f}s[/green]"
    elif lat <= 15.0:
        return f"[yellow]🟡 {lat:.1f}s[/yellow]"
    else:
        return f"[bold red]🔴 {lat:.1f}s[/bold red]"


def fetch_server_stats(
    host: str,
    query: str | None = None,
    api_token: str | None = None,
    timeout: int = 10,
    repo_root: Path | None = None,
    refresh_cache: bool = False,
    cache_ttl_hours: float = 24.0,
) -> dict[str, Any]:
    """Retrieve counts of studies, collections, version and latency for a NADA server."""
    clean_host = host.replace("https://", "").replace("http://", "").strip("/")
    if "/" in clean_host:
        clean_host = clean_host.split("/", 1)[0]
    base_url = f"https://{clean_host}"
    headers = get_request_headers(host=clean_host, api_token=api_token, repo_root=repo_root)

    target_repo = repo_root or Path.cwd() / ".cache"
    cache_file = target_repo / clean_host / ".stats_cache.json"

    if cache_file.exists() and not refresh_cache:
        try:
            with open(cache_file, encoding="utf-8") as f:
                cdata = json.load(f)
                cached_at_str = cdata.get("cached_at")
                if cached_at_str:
                    dt = datetime.fromisoformat(cached_at_str)
                    age_hours = (datetime.now(UTC) - dt).total_seconds() / 3600.0
                    if age_hours < cache_ttl_hours:
                        cdata["cached"] = True
                        return cdata
        except Exception:
            pass

    stats: dict[str, Any] = {
        "server": clean_host,
        "studies": 0,
        "collections": 0,
        "is_nada": False,
        "requires_token": False,
        "error": None,
        "version": None,
        "latency_seconds": None,
    }

    t0 = time.time()
    try:
        # 1. Probe collections endpoint
        coll_url = f"{base_url}/index.php/api/catalog/collections"
        r_coll = requests.get(coll_url, headers=headers, timeout=timeout)
        if r_coll.status_code == 200:
            stats["is_nada"] = True
            try:
                coll_data = r_coll.json()
                if isinstance(coll_data, list):
                    stats["collections"] = len(coll_data)
                elif isinstance(coll_data, dict) and "result" in coll_data:
                    stats["collections"] = len(coll_data["result"])
            except Exception:
                pass
        elif r_coll.status_code == 401:
            stats["is_nada"] = True
            stats["requires_token"] = True

        # 2. Probe search endpoint for total studies
        search_q = urllib.parse.quote(query) if query else "*"
        search_url = f"{base_url}/index.php/api/catalog/search?sk={search_q}&ps=1"
        r_search = requests.get(search_url, headers=headers, timeout=timeout)
        if r_search.status_code == 200:
            stats["is_nada"] = True
            stats["version"] = "5"
            try:
                s_data = r_search.json()
                res = s_data.get("result", {})
                stats["studies"] = res.get("total", len(res.get("rows", [])))
            except Exception:
                pass
        elif not stats["is_nada"]:
            # Check v4 export CSV
            v4_url = f"{base_url}/index.php/catalog/export/csv?ps=1"
            r_v4 = requests.get(v4_url, headers=headers, timeout=timeout)
            if r_v4.status_code == 200 and ("id,surveyid" in r_v4.text or "\ufeffid,surveyid" in r_v4.text):
                stats["is_nada"] = True
                stats["version"] = "4"

        stats["latency_seconds"] = round(time.time() - t0, 2)

    except requests.exceptions.Timeout:
        stats["latency_seconds"] = round(time.time() - t0, 2)
        stats["error"] = f"Timeout ({timeout}s)"
    except Exception as e:
        stats["latency_seconds"] = round(time.time() - t0, 2)
        stats["error"] = str(e)

    # Cache results
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        stats_to_save = dict(stats)
        stats_to_save["cached_at"] = datetime.now(UTC).isoformat()
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(stats_to_save, f, indent=2)
    except Exception:
        pass

    return stats


def display_server_stats(
    server: str = "ALL",
    country: str | None = None,
    query: str | None = None,
    api_token: str | None = None,
    refresh_cache: bool = False,
    cache_ttl_hours: float = 24.0,
    timeout: int = 10,
    repo_root: Path | None = None,
) -> None:
    """Display live study and collection counts for NADA servers."""
    installations = fetch_nada_installations(
        target_server=None if server.upper() == "ALL" else server,
        country=country,
        fallback_unlisted=True,
    )

    if not installations:
        console.print(f"[bold red]No NADA servers found matching filter '{server}'.[/bold red]")
        return

    table = Table(
        title=f"NADA Server Statistics ({len(installations)} servers)",
        header_style="bold cyan",
        show_header=True,
    )
    table.add_column("Repository / Server", style="white")
    table.add_column("Country", style="green", justify="center")
    table.add_column("Ver", style="dim", justify="center")
    table.add_column("Studies", justify="right", style="cyan")
    table.add_column("Collections", justify="right", style="yellow")
    table.add_column("Latency", justify="center")
    table.add_column("Status", justify="left")

    def _query_stat(inst: ServerInstallation) -> tuple[ServerInstallation, dict[str, Any]]:
        s = fetch_server_stats(
            host=inst.clean_hostname,
            query=query,
            api_token=api_token,
            timeout=timeout,
            repo_root=repo_root,
            refresh_cache=refresh_cache,
            cache_ttl_hours=cache_ttl_hours,
        )
        return inst, s

    with console.status("[bold green]Querying NADA servers..."):
        with ThreadPoolExecutor(max_workers=min(8, len(installations))) as executor:
            futures = [executor.submit(_query_stat, inst) for inst in installations]
            for future in as_completed(futures):
                inst, stat = future.result()
                name_disp = inst.name or inst.clean_hostname
                clickable_host = f"[link={inst.url}]{name_disp}[/link]" if inst.url else name_disp
                cc = inst.country_code or "-"
                ver_disp = format_version(stat.get("version") or inst.version)
                studies_disp = f"{stat.get('studies', 0):,}" if stat.get("is_nada") else "-"
                colls_disp = f"{stat.get('collections', 0):,}" if stat.get("is_nada") else "-"

                lat = stat.get("latency_seconds")
                is_err = bool(stat.get("error"))
                is_nada = bool(stat.get("is_nada"))
                req_tok = bool(stat.get("requires_token"))

                lat_badge = format_response_latency(lat, is_online=is_nada, requires_token=req_tok, is_error=is_err)

                if stat.get("error"):
                    status_text = f"[red]{stat['error']}[/red]"
                elif req_tok:
                    status_text = "[yellow]Auth Required[/yellow]"
                elif stat.get("cached"):
                    status_text = "[dim cyan]Cached (24h)[/dim cyan]"
                else:
                    status_text = "[green]Online[/green]"

                table.add_row(clickable_host, cc, ver_disp, studies_disp, colls_disp, lat_badge, status_text)

    console.print(table)


def fetch_active_studies(
    host: str,
    query: str | None = None,
    since_date: str | None = None,
    limit: int | None = None,
    target_idno: str | None = None,
    server_dir: Path | None = None,
    refresh_catalog: bool = False,
    cache_ttl_hours: float = 24.0,
    verbose: bool = False,
    api_token: str | None = None,
    catalog_progress_callback=None,
) -> dict[str, dict[str, Any]]:
    """Retrieve catalog studies for a NADA server with 24h caching."""
    clean_host = host.replace("https://", "").replace("http://", "").strip("/")
    if "/" in clean_host:
        clean_host = clean_host.split("/", 1)[0]
    base_url = f"https://{clean_host}"
    headers = get_request_headers(host=clean_host, api_token=api_token)

    cache_file = (server_dir / ".catalog_cache.json") if server_dir else None

    # Check local cache
    if cache_file and cache_file.exists() and not refresh_catalog and not target_idno:
        try:
            with open(cache_file, encoding="utf-8") as f:
                cdata = json.load(f)
                cached_at_str = cdata.get("cached_at")
                if cached_at_str:
                    dt = datetime.fromisoformat(cached_at_str)
                    age_hours = (datetime.now(UTC) - dt).total_seconds() / 3600.0
                    if age_hours < cache_ttl_hours:
                        cached_studies = cdata.get("studies", {})
                        if limit:
                            filtered = dict(list(cached_studies.items())[:limit])
                            return filtered
                        return cached_studies
        except Exception:
            pass

    active_studies: dict[str, dict[str, Any]] = {}

    if target_idno:
        # Directly request target study
        clean_idno = target_idno.strip()
        active_studies[clean_idno] = {
            "idno": clean_idno,
            "id": clean_idno,
            "title": clean_idno,
            "changed": None,
        }
        return active_studies

    # Discover studies using Catalog Search API
    page = 1
    per_page = 100
    sk_query = query if query else "*"

    while True:
        url = f"{base_url}/index.php/api/catalog/search?sk={urllib.parse.quote(sk_query)}&ps={per_page}&page={page}"
        if since_date:
            url += f"&created={urllib.parse.quote(since_date)}"

        if verbose:
            console.print(f"[dim]  [API] GET {url}[/dim]")
        file_logger.log(f"[API] GET {url}")

        try:
            r = requests.get(url, headers=headers, timeout=20)
            if r.status_code != 200:
                break
            data = r.json()
            res = data.get("result", {})
            rows = res.get("rows", [])
            total = res.get("total", len(rows))

            if not rows:
                break

            for row in rows:
                idno = str(row.get("idno") or row.get("id", "")).strip()
                if not idno:
                    continue
                active_studies[idno] = {
                    "idno": idno,
                    "id": str(row.get("id", "")),
                    "title": row.get("title", idno),
                    "nation": row.get("nation", ""),
                    "created": row.get("created"),
                    "changed": row.get("changed") or row.get("created"),
                    "form_model": row.get("form_model"),
                }
                if limit and len(active_studies) >= limit:
                    break

            if catalog_progress_callback:
                catalog_progress_callback(len(active_studies), min(total, limit) if limit else total)

            if limit and len(active_studies) >= limit:
                break

            if len(rows) < per_page or len(active_studies) >= total:
                break

            page += 1
        except Exception as e:
            file_logger.log(f"[API] Search query error: {e}", level="ERROR")
            break

    # Write to local cache
    if cache_file and active_studies:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_data = {
                "server": clean_host,
                "cached_at": datetime.now(UTC).isoformat(),
                "query": query,
                "count": len(active_studies),
                "studies": active_studies,
            }
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2)
        except Exception:
            pass

    return active_studies


def fetch_study_record(
    host: str,
    idno: str,
    metadata_format: str = "ddi",
    verbose: bool = False,
    api_token: str | None = None,
) -> tuple[bytes | None, str, str | None]:
    """Fetch study metadata in the specified format (ddi, json, resources).
    Returns (content_bytes, file_extension, error_reason).
    """
    clean_host = host.replace("https://", "").replace("http://", "").strip("/")
    if "/" in clean_host:
        clean_host = clean_host.split("/", 1)[0]
    base_url = f"https://{clean_host}"
    fmt = metadata_format.strip().lower()
    headers = get_request_headers(host=clean_host, api_token=api_token)
    encoded_idno = urllib.parse.quote(idno)

    if fmt in ("ddi", "xml", "ddi-c"):
        url = f"{base_url}/index.php/api/catalog/ddi/{encoded_idno}"
        ext = ".ddi.xml"
    elif fmt in ("json", "metadata"):
        url = f"{base_url}/index.php/api/catalog/json/{encoded_idno}"
        ext = ".nada.json"
    elif fmt == "resources":
        url = f"{base_url}/index.php/api/catalog/resources/{encoded_idno}"
        ext = ".resources.json"
    else:
        return None, f".{fmt}", f"Unsupported format '{fmt}'"

    if verbose:
        console.print(f"[dim]  [API] GET {url}[/dim]")
    file_logger.log(f"[API] GET {url}")

    try:
        r = requests.get(url, headers=headers, timeout=25)
        if r.status_code == 200:
            content = r.content
            if content and len(content.strip()) > 0:
                return content, ext, None
            return None, ext, "Empty metadata response from server (0 bytes)"
        err_msg = _extract_http_error_reason(r)
        return None, ext, err_msg
    except Exception as e:
        return None, ext, str(e)


class ServerHarvester:
    """Manages harvesting, diff detection, and file persistence for a single NADA server."""

    def __init__(self, host: str, repo_root: Path):
        self.host = host.replace("https://", "").replace("http://", "").strip("/")
        if "/" in self.host:
            self.host = self.host.split("/", 1)[0]
        self.server_dir = repo_root / self.host
        self.manifest_file = self.server_dir / ".manifest.json"
        self.manifest = self._load_manifest()

    def _load_manifest(self) -> dict[str, Any]:
        if self.manifest_file.exists():
            try:
                with open(self.manifest_file, encoding="utf-8") as f:
                    data = json.load(f)
                    if "errors" not in data:
                        data["errors"] = {}
                    if "records" not in data:
                        data["records"] = {}
                    return data
            except Exception:
                pass
        return {
            "server": self.host,
            "last_synced_at": None,
            "records": {},
            "errors": {},
        }

    def _save_manifest(self) -> None:
        self.server_dir.mkdir(parents=True, exist_ok=True)
        self.manifest["last_synced_at"] = datetime.now(UTC).isoformat()
        with open(self.manifest_file, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f, indent=2)

    def sync(
        self,
        query: str | None = None,
        since_date: str | None = None,
        dry_run: bool = False,
        limit: int | None = None,
        metadata_formats: str | list[str] = "ddi",
        verbose: bool = False,
        target_idno: str | None = None,
        refresh_catalog: bool = False,
        cache_ttl_hours: float = 24.0,
        retry_errors: bool = False,
        force_verify: bool = False,
        api_token: str | None = None,
        progress_callback=None,
        catalog_progress_callback=None,
    ) -> dict[str, list[Any]]:
        """Perform intelligent incremental sync (Additions, Updates, Deletions)."""
        effective_limit = None if limit == 0 else limit
        stats: dict[str, list[Any]] = {
            "datasets": [],
            "added": [],
            "updated": [],
            "deleted": [],
            "unchanged": [],
            "errors": [],
        }
        target_formats = normalize_formats(metadata_formats)

        if verbose:
            console.print(f"[bold cyan]── Syncing Server: {self.host} ──[/bold cyan]")

        active_studies = fetch_active_studies(
            self.host,
            query=query,
            since_date=since_date,
            limit=effective_limit,
            target_idno=target_idno,
            server_dir=self.server_dir,
            refresh_catalog=refresh_catalog,
            cache_ttl_hours=cache_ttl_hours,
            verbose=verbose,
            api_token=api_token,
            catalog_progress_callback=catalog_progress_callback,
        )
        stats["datasets"] = list(active_studies.keys())

        manifest_records = self.manifest.get("records", {})
        existing_keys = set(manifest_records.keys())
        current_active_pids = set(active_studies.keys())

        # Prune Deletions (only on unconstrained full sync)
        can_prune_deletions = (
            not target_idno and effective_limit is None and not query and not since_date and bool(active_studies)
        )
        if can_prune_deletions:
            for key in list(existing_keys):
                rec_info = manifest_records.get(key, {})
                pid = rec_info.get("global_id") or key.split("::")[0]
                if pid not in current_active_pids:
                    rel_path = rec_info.get("path") or rec_info.get("filename")
                    file_path = self.server_dir / rel_path if rel_path else None

                    stats["deleted"].append(key)
                    msg = f"DELETED: {key} -> removing {rel_path}"
                    if verbose:
                        console.print(f"  [bold red][-] {msg}[/bold red]")
                    file_logger.log(msg, level="DELETE")

                    if not dry_run:
                        if file_path and file_path.exists():
                            file_path.unlink()
                            meta_dir = file_path.parent
                            if meta_dir.exists() and not any(meta_dir.iterdir()):
                                meta_dir.rmdir()
                            study_dir = meta_dir.parent
                            if study_dir.exists() and study_dir != self.server_dir and not any(study_dir.iterdir()):
                                study_dir.rmdir()

                        if key in self.manifest["records"]:
                            del self.manifest["records"][key]

        total_items = len(active_studies) * len(target_formats)
        current_item = 0

        for idno, meta in active_studies.items():
            for fmt in target_formats:
                current_item += 1
                if progress_callback:
                    progress_callback(current_item, total_items, idno, fmt, meta)

                rec_key = f"{idno}::{fmt}"

                # Check non-recoverable error in manifest
                manifest_errors = self.manifest.get("errors", {})
                if rec_key in manifest_errors and not retry_errors:
                    cached_err = manifest_errors[rec_key].get("reason", "Non-recoverable error")
                    stats["errors"].append(
                        {
                            "key": rec_key,
                            "pid": idno,
                            "name": meta.get("title"),
                            "format": fmt,
                            "reason": f"[Manifest Cached Error] {cached_err}",
                        }
                    )
                    continue

                ext = get_format_extension(fmt)
                file_path, rel_path = get_dataset_metadata_path(self.server_dir, idno, ext=ext)
                is_new = rec_key not in existing_keys or not file_path.exists()

                # Fast Timestamp Check
                meta_changed = meta.get("changed") or meta.get("created")
                prev_record = manifest_records.get(rec_key, {})
                prev_changed = prev_record.get("updated_at")

                if not is_new and meta_changed and prev_changed and meta_changed == prev_changed and not force_verify:
                    stats["unchanged"].append(rec_key)
                    continue

                action = "added" if is_new else "updated"

                if dry_run:
                    stats[action].append(rec_key)
                    continue

                # Fetch record
                content_bytes, _, err_msg = fetch_study_record(
                    self.host, idno, metadata_format=fmt, verbose=verbose, api_token=api_token
                )

                if content_bytes:
                    content_hash = hashlib.sha256(content_bytes).hexdigest()
                    prev_hash = manifest_records.get(rec_key, {}).get("sha256")
                    if not is_new and prev_hash == content_hash:
                        stats["unchanged"].append(rec_key)
                        self.manifest["records"][rec_key]["updated_at"] = meta_changed
                        continue

                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(file_path, "wb") as f:
                        f.write(content_bytes)

                    self.manifest["records"][rec_key] = {
                        "global_id": idno,
                        "path": rel_path,
                        "filename": file_path.name,
                        "format": fmt,
                        "sha256": content_hash,
                        "updated_at": meta_changed,
                        "synced_at": datetime.now(UTC).isoformat(),
                    }
                    if rec_key in self.manifest["errors"]:
                        del self.manifest["errors"][rec_key]

                    stats[action].append(rec_key)
                else:
                    stats["errors"].append(
                        {
                            "key": rec_key,
                            "pid": idno,
                            "name": meta.get("title"),
                            "format": fmt,
                            "reason": err_msg or "Unknown error",
                        }
                    )
                    if is_non_recoverable_error(err_msg):
                        self.manifest["errors"][rec_key] = {
                            "pid": idno,
                            "format": fmt,
                            "reason": err_msg,
                            "failed_at": datetime.now(UTC).isoformat(),
                        }

        if not dry_run:
            self._save_manifest()

        return stats


@app.command(name="harvest")
def harvest(
    server: Annotated[
        str,
        typer.Option(
            "--server",
            "-s",
            envvar="NADA_SERVER",
            help="Target NADA server hostname (e.g. microdata.worldbank.org, nada.ubos.org) or ALL.",
        ),
    ] = "ALL",
    country: Annotated[
        str | None,
        typer.Option(
            "--country",
            "-c",
            help="Filter servers by 2-letter ISO code (e.g. US, ID, ZA, UG) or country name.",
        ),
    ] = None,
    query: Annotated[
        str | None,
        typer.Option(
            "--query",
            "-q",
            help="Keyword query for searching study metadata.",
        ),
    ] = None,
    since_date: Annotated[
        str | None,
        typer.Option(
            "--since",
            help="Harvest records created on or after date (YYYY-MM-DD).",
        ),
    ] = None,
    study_id: Annotated[
        str | None,
        typer.Option(
            "--id",
            "--idno",
            help="Harvest a single specific study by IDNO.",
        ),
    ] = None,
    formats: Annotated[
        str,
        typer.Option(
            "--formats",
            "-f",
            help="Metadata formats to harvest (comma-separated: ddi, json, resources, or all).",
        ),
    ] = "ddi",
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            "-l",
            help="Maximum number of studies to harvest per server (0 for unlimited).",
        ),
    ] = 25,
    repo_dir: Annotated[
        str | None,
        typer.Option(
            "--repo-dir",
            "-r",
            envvar="DARTFX_NADA_REPOSITORY",
            help="Local repository directory for harvested files.",
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
            help="Force catalog cache refresh from server.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Simulate harvest without writing files to disk.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Enable verbose output and HTTP request logging.",
        ),
    ] = False,
) -> None:
    """Harvest and incrementally sync NADA metadata records into local storage."""
    base_repo = Path(repo_dir).resolve() if repo_dir else Path.cwd() / "repository"
    base_repo.mkdir(parents=True, exist_ok=True)

    installations = fetch_nada_installations(
        target_server=None if server.upper() == "ALL" else server,
        country=country,
        fallback_unlisted=True,
    )

    if not installations:
        console.print(f"[bold red]No NADA installations found matching '{server}'.[/bold red]")
        raise typer.Exit(code=1)

    console.print(
        Panel(
            f"[bold cyan]NADA Metadata Harvester[/bold cyan]\n"
            f"Servers: {len(installations)} | Formats: {formats} | Limit: {limit or 'Unlimited'}\n"
            f"Storage: {base_repo}",
            expand=False,
        )
    )

    summary_table = Table(title="Harvest Summary", header_style="bold magenta", show_header=True)
    summary_table.add_column("Server", style="cyan")
    summary_table.add_column("Studies", justify="right", style="white")
    summary_table.add_column("Added", justify="right", style="green")
    summary_table.add_column("Updated", justify="right", style="yellow")
    summary_table.add_column("Unchanged", justify="right", style="dim")
    summary_table.add_column("Errors", justify="right", style="red")

    for inst in installations:
        host = inst.clean_hostname
        harvester = ServerHarvester(host, base_repo)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as prog:
            task = prog.add_task(f"Syncing {host}...", total=None)

            def update_progress(cur, total, pid, fmt, _meta, t_id=task, t_host=host):
                prog.update(t_id, total=total, completed=cur, description=f"{t_host}: {pid} ({fmt})")

            def update_cat(cur, total, t_id=task, t_host=host):
                prog.update(t_id, total=total, completed=cur, description=f"{t_host}: Catalog ({cur}/{total})")

            results = harvester.sync(
                query=query,
                since_date=since_date,
                dry_run=dry_run,
                limit=limit,
                metadata_formats=formats,
                verbose=verbose,
                target_idno=study_id,
                refresh_catalog=refresh,
                api_token=api_token,
                progress_callback=update_progress,
                catalog_progress_callback=update_cat,
            )

        summary_table.add_row(
            host,
            str(len(results.get("datasets", []))),
            str(len(results.get("added", []))),
            str(len(results.get("updated", []))),
            str(len(results.get("unchanged", []))),
            str(len(results.get("errors", []))),
        )

    console.print(summary_table)


if __name__ == "__main__":
    app()
