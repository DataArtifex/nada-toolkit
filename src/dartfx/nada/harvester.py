#!/usr/bin/env python3
"""
NADA Metadata Harvester & Sync Engine

A smart, incremental harvester for NADA DDI CodeBook and survey metadata records.
Uses Typer for CLI interface and Rich for terminal visualizations.
"""

import csv
import hashlib
import io
import json
import os
import re
import socket
import ssl
import sys
import threading
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
    TimeElapsedColumn,
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
        self._lock = threading.Lock()
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
            with self._lock:
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


def format_php_version(
    php_ver: str | None,
    nada_ver: str | int | float | None = None,
    is_online: bool = True,
) -> str:
    """Format detected PHP version with security/lifecycle color coding.

    - PHP < 8.0: Red (Critical End-of-Life)
    - PHP 8.0 - 8.1: Yellow (End-of-Life)
    - PHP >= 8.2: Green (Active / Security Support)
    - None / Hidden: Dim 'Hidden' or dim red '~5.x-7.x' when running legacy NADA v4
    """
    if not is_online:
        return "-"
    if not php_ver:
        if str(nada_ver) == "4":
            return "[dim red]~5.x-7.x[/dim red]"
        return "[dim]Hidden[/dim]"

    parts = str(php_ver).split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return str(php_ver)

    if major < 8:
        return f"[bold red]{php_ver}[/bold red]"
    elif major == 8 and minor < 2:
        return f"[yellow]{php_ver}[/yellow]"
    else:
        return f"[green]{php_ver}[/green]"


def get_server_dir_name(host: str) -> str:
    """Extract filesystem-safe directory name for a server by stripping protocol, path, and port.

    Colons from ports (e.g. 'microdata.ubos.org:7070') are invalid in directory names on Windows
    and NTFS/FAT/exFAT filesystems. Stripping the port yields 'microdata.ubos.org'.
    """
    clean = host.replace("https://", "").replace("http://", "").strip("/")
    if "/" in clean:
        clean = clean.split("/", 1)[0]
    if ":" in clean:
        clean = clean.split(":", 1)[0]
    return clean


def parse_server_url(host: str) -> tuple[str, str, str, str]:
    """Parse server host/URL string into (base_url, clean_host, scheme, subpath).

    Preserves schemes (http vs https) and subpaths (e.g. '/nada', '/surveydata').
    """
    raw = host.strip()
    is_explicit_http = raw.lower().startswith("http://")
    is_explicit_https = raw.lower().startswith("https://")

    if not is_explicit_http and not is_explicit_https:
        raw = f"https://{raw}"

    parsed = urllib.parse.urlparse(raw)
    clean_host = parsed.hostname or (parsed.netloc.split(":")[0] if ":" in parsed.netloc else parsed.netloc)
    port_suffix = f":{parsed.port}" if parsed.port else ""
    subpath = parsed.path.rstrip("/")
    if subpath == "/":
        subpath = ""

    scheme = "http" if is_explicit_http else (parsed.scheme or "https")
    base_url = f"{scheme}://{clean_host}{port_suffix}{subpath}"
    return base_url, clean_host, scheme, subpath


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
    "croissant": ".croissant.json",
    "schema": ".schema.jsonld",
    "schema.org": ".schema.jsonld",
    "json-ld": ".schema.jsonld",
}

ALL_SUPPORTED_FORMATS: list[str] = ["ddi", "json", "resources", "croissant", "schema"]


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
            elif fmt_clean in ("schema.org", "json-ld", "jsonld"):
                fmt_clean = "schema"
            if fmt_clean in ALL_SUPPORTED_FORMATS and fmt_clean not in parsed:
                parsed.append(fmt_clean)
    return parsed


DEFAULT_REQUEST_HEADERS: dict[str, str] = {
    "User-Agent": (
        f"Mozilla/5.0 (compatible; dartfx-nada/{__version__}; +https://github.com/DataArtifex/nada-toolkit)"
    ),
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
            dir_name = get_server_dir_name(clean_host)
            for s_name in (dir_name, clean_host):
                stf = Path(d) / s_name / ".api_token"
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
        server_dir = repo_root / get_server_dir_name(clean_host)
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
    custom_ua = os.environ.get("NADA_USER_AGENT") or os.environ.get("DARTFX_NADA_USER_AGENT")
    if custom_ua:
        headers["User-Agent"] = custom_ua.strip()
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
    if any(
        k in r_low
        for k in [
            "perimeter url block",
            "perimeter block",
            "geo/ip",
            "geographic restriction",
            "geographically restricted",
            "country block",
            "blocked by ip",
        ]
    ):
        return "HTTP 403: Perimeter Block (Geo/IP)"

    if any(k in r_low for k in ["restricted", "permission denied", "requires authentication"]):
        return "HTTP 403: Restricted Study (Authentication Required)"

    # 4. Bot Protection / WAF
    if any(
        k in r_low
        for k in ["cloudflare", "bot protection", "waf", "captcha", "ddos-guard", "incapsula", "imperva", "awswaf"]
    ):
        return "HTTP 403: Forbidden / Bot Protection (WAF)"

    if "403" in r_low or "forbidden" in r_low:
        return "HTTP 403: Forbidden"

    # 5. Not Found / Deaccessioned / Retired
    if "404" in r_low or "not found" in r_low:
        return "HTTP 404: Study / Endpoint Not Found"
    if "410" in r_low or "deaccessioned" in r_low or "gone" in r_low or "retired" in r_low:
        if "catalog" in r_low or "retired" in r_low:
            return "HTTP 410: Catalog Retired"
        return "HTTP 410: Study Deaccessioned"

    # 6. Unprocessable
    if "422" in r_low or "unprocessable" in r_low:
        return "HTTP 422: Unprocessable Entity"

    # 7. Server Errors
    if "502" in r_low or "bad gateway" in r_low:
        return "HTTP 502: Bad Gateway"
    if "503" in r_low or "service unavailable" in r_low:
        return "HTTP 503: Service Unavailable"
    if "504" in r_low or "gateway timeout" in r_low:
        return "HTTP 504: Gateway Timeout"
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

    # 8. SSL / TLS Diagnostics (checked before generic network errors)
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
        if "hostname" in r_low and ("mismatch" in r_low or "doesn't match" in r_low or "not valid" in r_low):
            return "Network: SSL Hostname Mismatch"
        if "self-signed" in r_low or "self signed" in r_low:
            return "Network: SSL Self-Signed Certificate"
        return "Network: SSL / TLS Certificate Error"

    # 9. Network Errors
    if any(k in r_low for k in ["timeout", "timed out", "connecttimeout", "readtimeout"]):
        return "Network: Request Timeout"

    if any(
        k in r_low
        for k in [
            "connection refused",
            "connection error",
            "max retries exceeded",
            "failed to establish a new connection",
            "nameresolutionerror",
            "getaddrinfo failed",
            "nodename nor servname",
        ]
    ):
        return "Network: Connection Failure"

    # 10. Parse Errors
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

    return "Other Error"


def abbreviate_error(reason: str | None) -> str:
    """Format a verbose error or exception string into a concise label for CLI table display."""
    if not reason:
        return "Unknown Error"
    r_low = reason.lower()

    # 0. Domain Parked / Expired / For Sale or Not NADA
    if any(k in r_low for k in ["hugedomains", "for sale", "domain expired", "domain parked", "parked domain"]):
        return "Domain For Sale"
    if "not a nada server" in r_low or "not nada server" in r_low or "not nada" in r_low:
        return "Not NADA Server"

    # Perimeter Block / Geo/IP
    if any(
        k in r_low
        for k in [
            "perimeter url block",
            "perimeter block",
            "geo/ip",
            "geographic restriction",
            "geographically restricted",
            "country block",
            "blocked by ip",
        ]
    ):
        return "Perimeter Block (Geo/IP)"

    # Cloudflare Challenge (Interactive Turnstile / JavaScript verification)
    if any(k in r_low for k in ["cloudflare challenge", "turnstile", "cf-mitigated"]):
        return "Cloudflare Challenge"

    # 1. SSL / TLS Diagnostics
    if "ssl" in r_low or "certificate" in r_low or "cert" in r_low:
        if "hostname" in r_low and ("mismatch" in r_low or "doesn't match" in r_low or "not valid" in r_low):
            return "SSL Hostname Mismatch"
        if "expired" in r_low:
            return "SSL Certificate Expired"
        if "self-signed" in r_low or "self signed" in r_low:
            return "SSL Self-Signed Cert"
        if (
            "unable to get local issuer certificate" in r_low
            or "unable to verify the first certificate" in r_low
            or "missing intermediate" in r_low
            or "incomplete chain" in r_low
        ):
            return "SSL Incomplete Chain"
        return "SSL Certificate Error"

    # 2. Timeout (preserve concise format like "Timeout (10s)" if already present)
    m = re.search(r"timeout\s*\(\d+s\)", reason, re.IGNORECASE)
    if m:
        return m.group(0).capitalize()
    if any(k in r_low for k in ["timeout", "timed out", "connecttimeout", "readtimeout"]):
        return "Request Timeout"

    # 3. DNS / Name Resolution
    if any(
        k in r_low
        for k in ["nameresolutionerror", "getaddrinfo failed", "nodename nor servname", "name or service not known"]
    ):
        return "DNS Resolution Failure"

    # 4. Connection Failures
    if "connection refused" in r_low:
        return "Connection Refused"
    if "connection reset" in r_low:
        return "Connection Reset"
    if any(
        k in r_low
        for k in [
            "connection error",
            "max retries exceeded",
            "failed to establish a new connection",
            "remotedisconnected",
        ]
    ):
        return "Connection Failure"

    # 5. Authentication / Forbidden
    if "401" in r_low or "unauthorized" in r_low:
        return "Auth Required (401)"
    if any(
        k in r_low
        for k in ["cloudflare", "bot protection", "waf", "captcha", "ddos-guard", "incapsula", "imperva", "awswaf"]
    ):
        return "Bot Protection (WAF)"
    if "403" in r_low or "forbidden" in r_low:
        return "Forbidden (403)"

    # 6. HTTP Status Codes
    if any(k in r_low for k in ["410", "retired", "catalog retired", "deaccessioned", "gone"]):
        return "Catalog Retired (410)"
    if "404" in r_low or "not found" in r_low:
        return "Not Found (404)"
    if "502" in r_low or "bad gateway" in r_low:
        return "Bad Gateway (502)"
    if "503" in r_low or "service unavailable" in r_low:
        return "Service Unavailable (503)"
    if "504" in r_low or "gateway timeout" in r_low:
        return "Gateway Timeout (504)"
    if "php server error" in r_low or "php fatal" in r_low:
        return "PHP Server Error"
    if "500" in r_low or "internal server error" in r_low:
        return "Server Error (500)"
    if any(k in r_low for k in ["5xx", "server error"]):
        return "Server Error (5xx)"

    # 7. Parse Errors
    if any(k in r_low for k in ["parseerror", "jsondecodeerror", "syntaxerror", "malformed"]):
        return "Malformed Response"

    # 8. Short clean string or fallback
    clean = reason.strip()
    if len(clean) <= 28 and not any(
        x in r_low for x in ["httpconnection", "httpsconnection", "traceback", "caused by", "socket"]
    ):
        return clean

    classified = classify_harvest_error(reason)
    if classified.startswith("Network: "):
        return classified[len("Network: ") :]
    if classified.startswith("HTTP "):
        return classified
    if classified != "Other Error":
        return classified

    return "Connection Failure"


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
        if clean_server:
            clean_srv = get_server_dir_name(srv)
            clean_target = get_server_dir_name(clean_server)
            if srv.lower() != clean_server.lower() and clean_srv.lower() != clean_target.lower():
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
        return f"[bold green]🟢⚡ {lat:.1f}s[/bold green]"
    elif lat <= 8.0:
        return f"[green]🟢 {lat:.1f}s[/green]"
    elif lat <= 15.0:
        return f"[yellow]🟡 {lat:.1f}s[/yellow]"
    else:
        return f"[bold red]🔴 {lat:.1f}s[/bold red]"


def _is_parked_or_domain_sale(orig_host: str, resp: requests.Response) -> bool:
    """Check if the response indicates the domain has expired, is parked, or is up for sale."""
    raw_url = getattr(resp, "url", None)
    if not isinstance(raw_url, str) or not raw_url:
        return False

    final_url = raw_url.lower()
    final_host = (urllib.parse.urlparse(raw_url).hostname or "").lower()

    parking_domains = [
        "hugedomains",
        "dan.com",
        "sedo.com",
        "sedoparking",
        "godaddy",
        "afternic",
        "parkingcrew",
        "bodis",
        "domainmarket",
        "namecheap",
    ]
    if any(p in final_url for p in parking_domains):
        return True

    # Check for domain mismatch on HTTP redirect
    history = getattr(resp, "history", None)
    if history and isinstance(history, list) and final_host:
        orig_clean = orig_host.lower().strip()
        orig_parts = orig_clean.split(".")
        final_parts = final_host.split(".")
        orig_base = ".".join(orig_parts[-2:]) if len(orig_parts) >= 2 else orig_clean
        final_base = ".".join(final_parts[-2:]) if len(final_parts) >= 2 else final_host
        if orig_base != final_base:
            headers = getattr(resp, "headers", {})
            ct = headers.get("content-type", "") if isinstance(headers, dict) else ""
            if "text/html" in str(ct).lower():
                raw_text = getattr(resp, "text", "")
                content_low = raw_text[:3000].lower() if isinstance(raw_text, str) else ""
                sale_keywords = [
                    "domain is for sale",
                    "buy this domain",
                    "domain has expired",
                    "parked free",
                    "domain parking",
                    "domain name may be for sale",
                    "inquire about this domain",
                ]
                if any(k in content_low for k in sale_keywords):
                    return True
                return True

    return False


def extract_server_environment(response: requests.Response | None) -> tuple[str | None, str | None]:
    """Extract detected PHP version and web server banner from HTTP response headers.

    Returns:
        tuple[str | None, str | None]: (php_version, web_server)
    """
    php_version: str | None = None
    web_server: str | None = None

    if response is None:
        return None, None

    headers = getattr(response, "headers", None)
    if not isinstance(headers, (dict, requests.structures.CaseInsensitiveDict)):
        return None, None

    # 1. Search for PHP version across standard headers
    for header_name in ("X-Powered-By", "Server", "X-PHP-Version"):
        val = headers.get(header_name)
        if not isinstance(val, str) or not val.strip():
            continue
        m = re.search(r"PHP[ /]([0-9]+(?:\.[0-9]+)+)", val, re.IGNORECASE)
        if m:
            php_version = m.group(1)
            break
        if header_name.lower() == "x-php-version":
            m_ver = re.search(r"([0-9]+(?:\.[0-9]+)+)", val)
            if m_ver:
                php_version = m_ver.group(1)
                break

    # 2. Extract Server banner
    server_hdr = headers.get("Server")
    if isinstance(server_hdr, str) and server_hdr.strip():
        s_clean = server_hdr.strip()
        # Strip PHP module info from server header if present
        s_clean = re.sub(r"\s*PHP/[0-9.]+(?:-[^\s]+)?", "", s_clean, flags=re.IGNORECASE).strip()
        if s_clean:
            web_server = s_clean

    return php_version, web_server


def _diagnose_probe_error(probes: list[requests.Response]) -> str:
    """Analyze probe responses to classify why a NADA server could not be resolved."""
    if not probes:
        return "Not NADA Server"

    # 1. Perimeter / Geo / IP Block
    for p in probes:
        txt = p.text.lower()
        if any(
            k in txt
            for k in [
                "perimeter url block",
                "perimeter block",
                "access denied from your location",
                "geographic restriction",
                "geographically restricted",
                "country block",
                "access from your country is blocked",
                "blocked by ip",
                "ip not allowed",
                "access denied by security policy",
            ]
        ):
            return "Perimeter Block (Geo/IP)"

    # 2. Cloudflare Challenge (Interactive Turnstile / JavaScript verification)
    for p in probes:
        hdr_server = p.headers.get("Server", "").lower()
        cf_mitigated = p.headers.get("cf-mitigated", "").lower()
        txt = p.text.lower()
        if (
            cf_mitigated == "challenge"
            or (
                "cloudflare" in hdr_server
                and any(
                    k in txt
                    for k in [
                        "just a moment",
                        "challenge-platform",
                        "turnstile",
                        "challenges.cloudflare.com",
                        "cf-browser-verification",
                        "attention required! | cloudflare",
                    ]
                )
            )
            or any(
                k in txt
                for k in [
                    "just a moment...",
                    "challenges.cloudflare.com",
                    "turnstile",
                    "challenge-platform",
                ]
            )
        ):
            return "Cloudflare Challenge"

    # 3. Bot Protection / WAF / DDoS Mitigation
    for p in probes:
        txt = p.text.lower()
        hdr_server = p.headers.get("Server", "").lower()
        if p.status_code == 403 and (
            "cloudflare" in hdr_server
            or "awselb" in hdr_server
            or "x-amzn-waf-action" in p.headers
            or any(
                k in txt
                for k in [
                    "cloudflare",
                    "waf",
                    "ddos-guard",
                    "incapsula",
                    "imperva",
                    "awswaf",
                    "bot protection",
                    "web application firewall",
                    "mod_security",
                    "captcha",
                ]
            )
        ):
            return "Bot Protection (WAF)"

    # 4. Authentication Required (401)
    for p in probes:
        if p.status_code == 401 or any(
            k in p.text.lower() for k in ["api token required", "authentication required", "unauthorized access"]
        ):
            return "Auth Required (401)"

    # 5. Catalog Retired / Decommissioned (410)
    for p in probes:
        txt = p.text.lower()
        if p.status_code == 410 or any(
            k in txt
            for k in [
                "410 gone",
                "catalog retired",
                "survey catalog retired",
                "service decommissioned",
                "archive retired",
            ]
        ):
            return "Catalog Retired (410)"

    # 6. Server Errors (5xx)
    for p in probes:
        if p.status_code == 502:
            return "Bad Gateway (502)"
        if p.status_code == 503:
            return "Service Unavailable (503)"
        if p.status_code == 504:
            return "Gateway Timeout (504)"
        if p.status_code == 500:
            return "Server Error (500)"
        if 500 <= p.status_code < 600:
            return f"Server Error ({p.status_code})"

    # 7. PHP Fatal Error on 200 responses
    for p in probes:
        txt = p.text.lower()
        if "fatal error" in txt or "uncaught error" in txt or "parse error" in txt or "failed opening required" in txt:
            return "PHP Server Error (500)"

    # 8. Standard 403 Forbidden
    for p in probes:
        if p.status_code == 403:
            return "Forbidden (403)"

    # 9. Endpoint Not Found (404)
    if all(p.status_code == 404 for p in probes):
        return "Not Found (404)"

    return "Not NADA Server"


def classify_server_availability(
    status: str | None = None,
    is_nada: bool = False,
    error: str | None = None,
    requires_token: bool = False,
) -> str:
    """Classify server health into 'accessible', 'restricted', or 'offline'.

    Categories:
        - 'accessible': Operational NADA server with publicly queryable catalog.
        - 'restricted': Live server where catalog queries are blocked by authentication,
                        perimeter firewalls, bot protection, or Cloudflare challenges.
        - 'offline': Inactive, decommissioned, DNS failure, timeout, non-NADA, or server error.

    Returns:
        str: 'accessible', 'restricted', or 'offline'.
    """
    clean_status = (status or "").lower()
    clean_error = (error or "").lower()
    combined = f"{clean_status} {clean_error}"

    # 1. Check for access restrictions on active endpoints
    if requires_token or any(
        k in combined
        for k in [
            "auth",
            "token",
            "forbidden",
            "401",
            "403",
            "cloudflare",
            "perimeter",
            "waf",
            "bot protection",
            "turnstile",
        ]
    ):
        return "restricted"

    # 2. Check for fully accessible operational NADA catalog
    if is_nada and not clean_error:
        return "accessible"

    if is_nada and any(s in clean_status for s in ["online", "cached", "http (ssl failed)"]):
        return "accessible"

    # 3. Otherwise, server is offline, non-NADA, retired, or hard error
    return "offline"


def get_ca_bundle_for_host(host: str, cache_dir: Path | None = None) -> str | None:
    """Return path to cached intermediate CA bundle for host if available.

    Args:
        host: Hostname or domain of the server.
        cache_dir: Optional custom directory where CA bundles are cached.

    Returns:
        str | None: String path to the bundle file, or None if not cached.
    """
    clean_host = host.split(":")[0].strip()
    target_cache = cache_dir or (Path.home() / ".cache" / "dartfx" / "nada" / "ca_bundles")
    bundle_path = target_cache / f"{clean_host}.pem"
    if bundle_path.exists() and bundle_path.stat().st_size > 0:
        return str(bundle_path)
    return None


def resolve_aia_intermediate_ca(
    host: str,
    port: int = 443,
    timeout: int = 5,
    cache_dir: Path | None = None,
) -> Path | None:
    """Attempt Authority Information Access (AIA) intermediate CA certificate fetching.

    When a server provides an incomplete TLS certificate chain (missing intermediate CA certificate),
    standard TLS libraries fail with SSLCertVerificationError ('unable to get local issuer certificate').
    This function connects to the server with verify_mode=CERT_NONE to inspect the leaf certificate's
    DER payload, extracts the Authority Information Access (AIA) CA Issuers URI, downloads the missing
    intermediate certificate(s), and returns the path to a combined PEM CA bundle (certifi + intermediate).

    Returns:
        Path | None: Path to the combined CA bundle file if successfully created, or None if unavailable.
    """
    clean_host = host.split(":")[0].strip()
    target_cache = cache_dir or (Path.home() / ".cache" / "dartfx" / "nada" / "ca_bundles")
    bundle_path = target_cache / f"{clean_host}.pem"

    if bundle_path.exists() and bundle_path.stat().st_size > 0:
        return bundle_path

    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        with socket.create_connection((clean_host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=clean_host) as ssock:
                der_cert = ssock.getpeercert(binary_form=True)

        if not der_cert:
            return None

        # Extract AIA CA Issuers HTTP URLs (ending in .crt or .cer) from DER
        matches = re.findall(rb"https?://[^\x00-\x20\x7f-\xff]+\.(?:crt|cer)", der_cert)
        if not matches:
            return None

        import certifi

        with open(certifi.where(), encoding="utf-8") as f:
            augmented_bundle = f.read()

        urls_to_fetch = [m.decode("ascii", errors="ignore") for m in matches]
        seen_urls = set(urls_to_fetch)
        fetched_pems: list[str] = []

        while urls_to_fetch:
            url = urls_to_fetch.pop(0)
            try:
                r = requests.get(url, timeout=timeout)
                if r.status_code == 200 and r.content:
                    content = r.content
                    if b"-----BEGIN CERTIFICATE-----" in content:
                        pem_cert = content.decode("utf-8", errors="ignore")
                    else:
                        pem_cert = ssl.DER_cert_to_PEM_cert(content)
                    fetched_pems.append(pem_cert)

                    # Look for parent intermediate AIA URLs within the intermediate DER
                    parent_matches = re.findall(rb"https?://[^\x00-\x20\x7f-\xff]+\.(?:crt|cer)", content)
                    for pm in parent_matches:
                        purl = pm.decode("ascii", errors="ignore")
                        if purl not in seen_urls:
                            seen_urls.add(purl)
                            urls_to_fetch.append(purl)
            except Exception:
                continue

        if not fetched_pems:
            return None

        augmented_bundle = f"{augmented_bundle}\n" + "\n".join(fetched_pems)
        target_cache.mkdir(parents=True, exist_ok=True)
        bundle_path.write_text(augmented_bundle, encoding="utf-8")
        return bundle_path

    except Exception:
        return None


def _probe_nada_url(
    base_url: str,
    clean_host: str,
    headers: dict[str, str],
    timeout: int,
    query: str | None = None,
    verify: str | bool | None = None,
) -> dict[str, Any]:
    """Probe a single base URL (http or https) for NADA API / catalog endpoints."""
    res: dict[str, Any] = {
        "is_nada": False,
        "studies": 0,
        "collections": 0,
        "version": None,
        "requires_token": False,
        "error": None,
        "raw_ssl_error": None,
        "is_ssl_error": False,
        "latency_seconds": None,
        "php_version": None,
        "web_server": None,
    }

    req_kwargs: dict[str, Any] = {"headers": headers, "timeout": timeout}
    if verify is not None:
        req_kwargs["verify"] = verify

    def _inspect_resp_env(resp: requests.Response | None) -> None:
        if resp is None:
            return
        php, srv = extract_server_environment(resp)
        if php and not res["php_version"]:
            res["php_version"] = php
        if srv and not res["web_server"]:
            res["web_server"] = srv

    t0 = time.time()
    try:
        # 1. Probe collections endpoint
        coll_url = f"{base_url}/index.php/api/catalog/collections"
        r_coll = requests.get(coll_url, **req_kwargs)
        _inspect_resp_env(r_coll)
        if _is_parked_or_domain_sale(clean_host, r_coll):
            res["error"] = "Domain For Sale"
        elif r_coll.status_code == 200:
            try:
                coll_data = r_coll.json()
                if isinstance(coll_data, list):
                    res["is_nada"] = True
                    res["collections"] = len(coll_data)
                elif isinstance(coll_data, dict):
                    if "result" in coll_data:
                        res["is_nada"] = True
                        if isinstance(coll_data["result"], list):
                            res["collections"] = len(coll_data["result"])
                        elif isinstance(coll_data["result"], dict):
                            res["collections"] = len(coll_data["result"])
                    elif "collections" in coll_data:
                        res["is_nada"] = True
                        if isinstance(coll_data["collections"], list):
                            res["collections"] = len(coll_data["collections"])
                    elif coll_data.get("status") in ("success", "ok"):
                        res["is_nada"] = True
            except Exception:
                pass
        elif r_coll.status_code == 401:
            res["is_nada"] = True
            res["requires_token"] = True

        r_search: requests.Response | None = None
        r_v4: requests.Response | None = None

        # 2. Probe search endpoint for total studies
        if res["error"] != "Domain For Sale":
            search_url = f"{base_url}/index.php/api/catalog/search?ps=1"
            if query:
                search_url += f"&sk={urllib.parse.quote(query)}"
            r_search = requests.get(search_url, **req_kwargs)
            _inspect_resp_env(r_search)
            if _is_parked_or_domain_sale(clean_host, r_search):
                res["error"] = "Domain For Sale"
                res["is_nada"] = False
            elif r_search.status_code == 200:
                try:
                    s_data = r_search.json()
                    if isinstance(s_data, dict) and (
                        "result" in s_data or "rows" in s_data or s_data.get("status") == "success"
                    ):
                        res["is_nada"] = True
                        res["version"] = "5"
                        res_dict = s_data.get("result", {})
                        if isinstance(res_dict, dict):
                            try:
                                res["studies"] = int(res_dict.get("total", len(res_dict.get("rows", []))))
                            except (ValueError, TypeError):
                                res["studies"] = len(res_dict.get("rows", []))
                        elif isinstance(res_dict, list):
                            res["studies"] = len(res_dict)
                except Exception:
                    pass
            elif r_search.status_code == 401:
                res["is_nada"] = True
                res["requires_token"] = True

        # 3. Probe v4 CSV export endpoint
        if not res["is_nada"] and res["error"] != "Domain For Sale":
            v4_url = f"{base_url}/index.php/catalog/export/csv?ps=1"
            r_v4 = requests.get(v4_url, **req_kwargs)
            _inspect_resp_env(r_v4)
            if _is_parked_or_domain_sale(clean_host, r_v4):
                res["error"] = "Domain For Sale"
            elif r_v4.status_code == 200:
                txt = r_v4.text
                if txt.startswith("\ufeff"):
                    txt = txt[1:]
                txt_low = txt[:50].lower()
                if "id,surveyid" in txt_low or '"id","surveyid"' in txt_low:
                    res["is_nada"] = True
                    res["version"] = "4"

        # 4. For v4 servers, extract total study count from catalog page HTML
        if res["is_nada"] and res.get("version") == "4" and res.get("studies", 0) == 0:
            try:
                cat_url = f"{base_url}/index.php/catalog"
                r_cat = requests.get(cat_url, **req_kwargs)
                _inspect_resp_env(r_cat)
                if r_cat.status_code == 200:
                    html_txt = r_cat.text
                    m_sc = re.search(
                        r'class=["\']search-count["\'][^>]*>(.*?)</div>',
                        html_txt,
                        re.IGNORECASE | re.DOTALL,
                    )
                    if m_sc:
                        nums = re.findall(r"<b>\s*([0-9,]+)\s*</b>", m_sc.group(1))
                        if nums:
                            clean_nums = [int(n.replace(",", "")) for n in nums if n.replace(",", "").isdigit()]
                            if clean_nums:
                                res["studies"] = max(clean_nums)
                    if res.get("studies", 0) == 0:
                        m_of = re.search(
                            r"(?:parmi|of|out of|de)\s+<b>\s*([0-9,]+)\s*</b>",
                            html_txt,
                            re.IGNORECASE,
                        )
                        if m_of:
                            val_str = m_of.group(1).replace(",", "")
                            if val_str.isdigit():
                                res["studies"] = int(val_str)
            except Exception:
                pass

        if not res["is_nada"] and not res["error"]:
            probes = [r for r in [r_search, r_coll, r_v4] if r is not None]
            # If all subpath probes returned 404 (or failed), also probe the base URL
            if not probes or all(p.status_code == 404 for p in probes):
                try:
                    r_home = requests.get(base_url, allow_redirects=True, **req_kwargs)
                    _inspect_resp_env(r_home)
                    if _is_parked_or_domain_sale(clean_host, r_home):
                        res["error"] = "Domain For Sale"
                    else:
                        probes.append(r_home)
                except Exception:
                    pass

            if not res["error"]:
                res["error"] = _diagnose_probe_error(probes)

        res["latency_seconds"] = round(time.time() - t0, 2)

    except requests.exceptions.SSLError as ssl_err:
        res["latency_seconds"] = round(time.time() - t0, 2)
        res["error"] = abbreviate_error(str(ssl_err))
        res["raw_ssl_error"] = str(ssl_err)
        res["is_ssl_error"] = True
    except requests.exceptions.Timeout:
        res["latency_seconds"] = round(time.time() - t0, 2)
        res["error"] = f"Timeout ({timeout}s)"
    except Exception as e:
        res["latency_seconds"] = round(time.time() - t0, 2)
        abbr = abbreviate_error(str(e))
        res["error"] = abbr
        res["is_ssl_error"] = (
            abbr.startswith("SSL ") or "ssl" in str(e).lower() or isinstance(e, requests.exceptions.SSLError)
        )

    return res


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
    base_url, clean_host, primary_scheme, subpath = parse_server_url(host)
    parsed = urllib.parse.urlparse(base_url)
    port_suffix = f":{parsed.port}" if parsed.port else ""

    headers = get_request_headers(host=clean_host, api_token=api_token, repo_root=repo_root)

    target_repo = repo_root or Path.cwd() / ".cache"
    cache_file = target_repo / get_server_dir_name(clean_host) / ".stats_cache.json"

    if cache_file.exists() and not refresh_cache:
        try:
            with open(cache_file, encoding="utf-8") as f:
                cdata = json.load(f)
                cached_at_str = cdata.get("cached_at")
                if cached_at_str:
                    dt = datetime.fromisoformat(cached_at_str)
                    age_hours = (datetime.now(UTC) - dt).total_seconds() / 3600.0
                    if age_hours < cache_ttl_hours:
                        # Auto-invalidate stale false-positive cache entries (e.g. 0-study 'is_nada=True')
                        if cdata.get("is_nada") and cdata.get("studies", 0) == 0 and not cdata.get("requires_token"):
                            pass  # Re-verify live using strict payload validation
                        else:
                            cdata["cached"] = True
                            return cdata
        except Exception:
            pass

    primary_url = base_url

    cached_bundle = get_ca_bundle_for_host(clean_host) if primary_scheme == "https" else None
    stats = _probe_nada_url(primary_url, clean_host, headers, timeout, query=query, verify=cached_bundle)
    stats["server"] = clean_host
    stats["scheme"] = primary_scheme
    if cached_bundle and stats.get("is_nada"):
        stats["aia_repaired"] = True
        stats["ca_bundle"] = cached_bundle

    # Enhancement 1: If server is on HTTP, check if HTTPS is available and recommend switching if fully functional
    if primary_scheme == "http" and stats.get("is_nada"):
        try:
            https_url = f"https://{clean_host}{port_suffix}{subpath}"
            https_stats = _probe_nada_url(https_url, clean_host, headers, timeout=min(5, timeout), query=query)
            if https_stats.get("is_nada") and not https_stats.get("is_ssl_error") and not https_stats.get("error"):
                stats["https_available"] = True
                stats["https_upgrade_recommended"] = True
                if not stats.get("php_version") and https_stats.get("php_version"):
                    stats["php_version"] = https_stats["php_version"]
                if not stats.get("web_server") and https_stats.get("web_server"):
                    stats["web_server"] = https_stats["web_server"]
        except Exception:
            pass

    # Enhancement 2: If primary was HTTPS and SSL failed, attempt AIA intermediate CA chasing
    if primary_scheme == "https" and stats.get("is_ssl_error"):
        err_str = str(stats.get("raw_ssl_error") or stats.get("error") or "")
        if "unable to get local issuer certificate" in err_str or "Incomplete Chain" in str(stats.get("error")):
            try:
                aia_bundle = resolve_aia_intermediate_ca(clean_host, port=parsed.port or 443, timeout=min(5, timeout))
                if aia_bundle:
                    aia_stats = _probe_nada_url(
                        primary_url,
                        clean_host,
                        headers,
                        timeout=timeout,
                        query=query,
                        verify=str(aia_bundle),
                    )
                    if aia_stats.get("is_nada"):
                        stats["is_nada"] = True
                        stats["studies"] = aia_stats["studies"]
                        stats["collections"] = aia_stats["collections"]
                        stats["version"] = aia_stats["version"]
                        stats["requires_token"] = aia_stats["requires_token"]
                        stats["aia_repaired"] = True
                        stats["ca_bundle"] = str(aia_bundle)
                        stats["is_ssl_error"] = False
                        stats["ssl_error"] = None
                        stats["error"] = None
                        stats["scheme"] = "https"
                        if aia_stats.get("php_version"):
                            stats["php_version"] = aia_stats["php_version"]
                        if aia_stats.get("web_server"):
                            stats["web_server"] = aia_stats["web_server"]
            except Exception:
                pass

    # Enhancement 3: If primary was HTTPS and SSL failed (and not repaired by AIA), check if we can fall back to HTTP
    if primary_scheme == "https" and not stats.get("is_nada") and stats.get("is_ssl_error"):
        try:
            http_url = f"http://{clean_host}{port_suffix}{subpath}"
            http_stats = _probe_nada_url(http_url, clean_host, headers, timeout=timeout, query=query)
            if http_stats.get("is_nada"):
                stats["is_nada"] = True
                stats["studies"] = http_stats["studies"]
                stats["collections"] = http_stats["collections"]
                stats["version"] = http_stats["version"]
                stats["requires_token"] = http_stats["requires_token"]
                stats["http_fallback"] = True
                stats["ssl_error"] = stats.get("error")
                stats["error"] = None
                stats["scheme"] = "http"
                if http_stats.get("php_version"):
                    stats["php_version"] = http_stats["php_version"]
                if http_stats.get("web_server"):
                    stats["web_server"] = http_stats["web_server"]
        except Exception:
            pass

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
    show_env: bool = False,
    sort_by: str = "name",
    descending: bool | None = None,
    format: str = "table",
    output: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Display live study and collection counts for NADA servers."""
    clean_fmt = (format or "table").strip().lower()

    out_path: Path | None = None
    if output:
        out_str = str(output).strip()
        if out_str.lower() in ("auto", "__auto__", "-"):
            ext_map = {
                "json": "json",
                "markdown": "md",
                "md": "md",
                "csv": "csv",
                "table": "txt",
            }
            ext = ext_map.get(clean_fmt, "txt")
            out_path = Path(f"dartfx-nada.stats.{ext}")
        else:
            out_path = Path(out_str)
        out_path.parent.mkdir(parents=True, exist_ok=True)

    installations = fetch_nada_installations(
        target_server=None if server.upper() == "ALL" else server,
        country=country,
        fallback_unlisted=True,
    )

    if not installations:
        if out_path:
            if clean_fmt == "json":
                out_path.write_text("[]\n", encoding="utf-8")
            elif clean_fmt in ("markdown", "md"):
                out_path.write_text("No NADA servers found.\n", encoding="utf-8")
            elif clean_fmt == "csv":
                out_path.write_text("", encoding="utf-8")
            else:
                out_path.write_text(f"No NADA servers found matching filter '{server}'.\n", encoding="utf-8")
            console.print(f"[bold green]✓ Statistics saved to:[/] {out_path}")
        else:
            if clean_fmt == "json":
                console.print("[]")
            elif clean_fmt in ("markdown", "md"):
                console.print("No NADA servers found.")
            elif clean_fmt == "table":
                console.print(f"[bold red]No NADA servers found matching filter '{server}'.[/bold red]")
        return []

    table = Table(
        title=f"NADA Server Statistics ({len(installations)} servers)",
        header_style="bold cyan",
        show_header=True,
    )
    table.add_column("Repository / Server", style="white")
    table.add_column("Country", style="green", justify="center")
    table.add_column("Ver", style="dim", justify="center")
    table.add_column("PHP", justify="center")
    if show_env:
        table.add_column("Web Server", style="dim", justify="left")
    table.add_column("Studies", justify="right", style="cyan")
    table.add_column("Latency", justify="center")
    table.add_column("Status", justify="left")

    def _query_stat(inst: ServerInstallation) -> tuple[ServerInstallation, dict[str, Any]]:
        host_to_query = inst.url or inst.clean_hostname
        s = fetch_server_stats(
            host=host_to_query,
            query=query,
            api_token=api_token,
            timeout=timeout,
            repo_root=repo_root,
            refresh_cache=refresh_cache,
            cache_ttl_hours=cache_ttl_hours,
        )
        return inst, s

    stat_results: list[tuple[ServerInstallation, dict[str, Any]]] = []

    stderr_console = Console(stderr=True)
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=stderr_console,
        transient=True,
    ) as prog:
        task = prog.add_task("[bold green]Querying NADA servers...", total=len(installations))
        with ThreadPoolExecutor(max_workers=min(12, len(installations))) as executor:
            future_to_inst = {executor.submit(_query_stat, inst): inst for inst in installations}
            for future in as_completed(future_to_inst):
                inst, stat = future.result()
                stat_results.append((inst, stat))
                prog.update(task, advance=1, description=f"[bold green]Querying:[/] {inst.clean_hostname}")

    def _format_count(val: Any) -> str:
        if val is None or val == "-":
            return "-"
        if isinstance(val, int | float):
            return f"{int(val):,}"
        if isinstance(val, str):
            clean = val.replace(",", "").strip()
            if clean.isdigit():
                return f"{int(clean):,}"
            return val
        return str(val)

    clean_sort = (sort_by or "name").strip().lower()
    if descending is not None:
        reverse_sort = descending
    elif clean_sort in ("studies", "count"):
        reverse_sort = True
    else:
        reverse_sort = False

    def _sort_key(item: tuple[ServerInstallation, dict[str, Any]]) -> Any:
        inst, stat = item
        if clean_sort in ("name", "server"):
            return (inst.name or inst.clean_hostname or "").lower()
        elif clean_sort in ("country", "cc"):
            return (inst.country_code or "ZZ").lower()
        elif clean_sort in ("studies", "count"):
            try:
                return int(stat.get("studies") or 0)
            except (ValueError, TypeError):
                return 0
        elif clean_sort in ("latency", "lat", "time"):
            lat = stat.get("latency_seconds")
            return float(lat) if lat is not None else 9999.0
        elif clean_sort in ("php", "php_version"):
            php = stat.get("php_version")
            if not php:
                return (0, 0, 0)
            try:
                parts = tuple(int(p) for p in str(php).split(".") if p.isdigit())
                return parts
            except Exception:
                return (0, 0, 0)
        elif clean_sort in ("status", "state"):
            return str(stat.get("error") or "online").lower()
        return (inst.name or inst.clean_hostname or "").lower()

    stat_results.sort(key=_sort_key, reverse=reverse_sort)

    records: list[dict[str, Any]] = []
    for inst, stat in stat_results:
        is_err = bool(stat.get("error"))
        is_nada = bool(stat.get("is_nada"))
        req_tok = bool(stat.get("requires_token"))
        lat = stat.get("latency_seconds")

        if not is_nada:
            if stat.get("error"):
                plain_status = abbreviate_error(str(stat["error"]))
            else:
                plain_status = "Not NADA Server"
        elif stat.get("aia_repaired"):
            plain_status = "Online (AIA Fixed)"
        elif stat.get("http_fallback"):
            plain_status = "HTTP (SSL Failed)"
        elif stat.get("https_upgrade_recommended"):
            plain_status = "Online (HTTPS Avail)"
        elif req_tok:
            plain_status = "Auth Required"
        elif stat.get("cached"):
            plain_status = "Online (Cached)"
        else:
            plain_status = "Online"

        ver_raw = stat.get("version") or inst.version
        ver_disp = format_version(ver_raw)
        php_raw = stat.get("php_version")

        raw_studies = stat.get("studies") if is_nada else None
        if raw_studies is not None:
            try:
                studies_int: int | None = int(str(raw_studies).replace(",", "").strip())
            except (ValueError, TypeError):
                studies_int = None
        else:
            studies_int = None

        availability = classify_server_availability(
            status=plain_status,
            is_nada=is_nada,
            error=stat.get("error"),
            requires_token=req_tok,
        )

        record: dict[str, Any] = {
            "name": inst.name or inst.clean_hostname,
            "url": inst.catalog_url or inst.url,
            "hostname": inst.clean_hostname,
            "country": inst.country,
            "country_code": inst.country_code,
            "version": ver_disp if ver_disp != "-" else None,
            "php_version": php_raw,
            "web_server": stat.get("web_server") if is_nada else None,
            "studies": studies_int,
            "latency_seconds": lat,
            "status": plain_status,
            "availability": availability,
            "is_nada": is_nada,
            "cached": bool(stat.get("cached")),
            "aia_repaired": bool(stat.get("aia_repaired")),
            "error": stat.get("error"),
        }
        records.append(record)

    if clean_fmt == "json":
        if out_path:
            out_path.write_text(json.dumps(records, indent=2, default=str) + "\n", encoding="utf-8")
            console.print(f"[bold green]✓ Statistics saved to:[/] {out_path}")
        else:
            console.print_json(json.dumps(records, default=str))
        return records

    if clean_fmt in ("markdown", "md"):
        md_lines: list[str] = []
        if show_env:
            md_lines.append("| Repository / Server | Country | Ver | PHP | Web Server | Studies | Latency | Status |")
            md_lines.append("|:---|:---:|:---:|:---:|:---|---:|:---:|:---|")
        else:
            md_lines.append("| Repository / Server | Country | Ver | PHP | Studies | Latency | Status |")
            md_lines.append("|:---|:---:|:---:|:---:|---:|:---:|:---|")

        for r, (_inst, stat) in zip(records, stat_results, strict=False):
            name_disp = (r["name"] or "").replace("|", "\\|")
            link_url = r["url"]
            repo_cell = f"[{name_disp}]({link_url})" if link_url else name_disp
            cc_cell = r["country_code"] or "-"
            ver_cell = r["version"] or "-"

            php_val = r["php_version"]
            if not r["is_nada"]:
                php_cell = "-"
            elif not php_val:
                php_cell = "~5.x-7.x" if r["version"] == "v4" else "Hidden"
            else:
                php_cell = php_val

            studies_cell = f"{r['studies']:,}" if r["studies"] is not None else "-"

            lat_badge = format_response_latency(
                r["latency_seconds"],
                is_online=r["is_nada"],
                requires_token=bool(stat.get("requires_token")),
                is_error=bool(stat.get("error")),
            )
            lat_cell = Text.from_markup(lat_badge).plain
            status_cell = r["status"]

            if show_env:
                web_srv = (r["web_server"] or "-").replace("|", "\\|")
                md_row = (
                    f"| {repo_cell} | {cc_cell} | {ver_cell} | {php_cell} "
                    f"| {web_srv} | {studies_cell} | {lat_cell} | {status_cell} |"
                )
            else:
                md_row = (
                    f"| {repo_cell} | {cc_cell} | {ver_cell} | {php_cell} "
                    f"| {studies_cell} | {lat_cell} | {status_cell} |"
                )
            md_lines.append(md_row)

        if len(records) > 1:
            accessible_count = sum(1 for r in records if r.get("availability") == "accessible")
            restricted_count = sum(1 for r in records if r.get("availability") == "restricted")
            offline_count = sum(1 for r in records if r.get("availability") == "offline")
            total_studies = sum(int(r.get("studies") or 0) for r in records if r.get("is_nada"))
            md_lines.append("")
            md_lines.append(
                f"_Summary: {accessible_count} accessible • {restricted_count} restricted • "
                f"{offline_count} offline/inactive ({len(records)} servers) • "
                f"{total_studies:,} total studies indexed._"
            )

        if out_path:
            out_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
            console.print(f"[bold green]✓ Statistics saved to:[/] {out_path}")
        else:
            print("\n".join(md_lines))
        return records

    if clean_fmt == "csv":
        fieldnames = [
            "name",
            "url",
            "hostname",
            "country",
            "country_code",
            "version",
            "php_version",
            "web_server",
            "studies",
            "latency_seconds",
            "status",
            "availability",
        ]
        if out_path:
            with open(out_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                for r in records:
                    writer.writerow(r)
            console.print(f"[bold green]✓ Statistics saved to:[/] {out_path}")
        else:
            writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for r in records:
                writer.writerow(r)
        return records

    # Default: Rich Table
    if len(records) > 1:
        accessible_count = sum(1 for r in records if r.get("availability") == "accessible")
        restricted_count = sum(1 for r in records if r.get("availability") == "restricted")
        offline_count = sum(1 for r in records if r.get("availability") == "offline")
        total_studies = sum(int(r.get("studies") or 0) for r in records if r.get("is_nada"))
        table.caption = (
            f"[bold]Total:[/] [green]{accessible_count}[/green] accessible"
            f" • [yellow]{restricted_count}[/yellow] restricted"
            f" • [red]{offline_count}[/red] offline/inactive"
            f" ({len(records)} servers)"
            f" • [cyan]{total_studies:,}[/cyan] studies indexed"
        )

    for _r, (inst, stat) in zip(records, stat_results, strict=False):
        name_disp = inst.name or inst.clean_hostname
        link_url = inst.catalog_url or inst.url
        clickable_host = f"[link={link_url}]{name_disp}[/link]" if link_url else name_disp
        cc = inst.country_code or "-"
        ver_disp = format_version(stat.get("version") or inst.version)
        php_disp = format_php_version(
            stat.get("php_version"),
            nada_ver=stat.get("version") or inst.version,
            is_online=bool(stat.get("is_nada")),
        )
        studies_disp = _format_count(stat.get("studies", 0)) if stat.get("is_nada") else "-"

        lat = stat.get("latency_seconds")
        is_err = bool(stat.get("error"))
        is_nada = bool(stat.get("is_nada"))
        req_tok = bool(stat.get("requires_token"))

        lat_badge = format_response_latency(lat, is_online=is_nada, requires_token=req_tok, is_error=is_err)

        if not is_nada:
            if stat.get("error"):
                abbr = abbreviate_error(str(stat["error"]))
                abbr_low = abbr.lower()
                if any(k in abbr_low for k in ["waf", "cloudflare", "perimeter", "auth", "token"]):
                    status_text = f"[yellow]{abbr}[/yellow]"
                elif any(k in abbr_low for k in ["retired", "for sale"]):
                    status_text = f"[magenta]{abbr}[/magenta]"
                elif "not nada" in abbr_low or "not found" in abbr_low:
                    status_text = f"[dim red]{abbr}[/dim red]"
                else:
                    status_text = f"[red]{abbr}[/red]"
            else:
                status_text = "[dim red]Not NADA Server[/dim red]"
        elif stat.get("aia_repaired"):
            status_text = "[cyan]Online (AIA Fixed)[/cyan]"
        elif stat.get("http_fallback"):
            status_text = "[yellow]HTTP (SSL Failed)[/yellow]"
        elif stat.get("https_upgrade_recommended"):
            status_text = "[cyan]Online (HTTPS Avail)[/cyan]"
        elif req_tok:
            status_text = "[yellow]Auth Required[/yellow]"
        elif stat.get("cached"):
            status_text = "[dim cyan]Online (Cached)[/dim cyan]"
        else:
            status_text = "[green]Online[/green]"

        row_items = [clickable_host, cc, ver_disp, php_disp]
        if show_env:
            srv_disp = (stat.get("web_server") or "-") if is_nada else "-"
            row_items.append(srv_disp)
        row_items.extend([studies_disp, lat_badge, status_text])

        table.add_row(*row_items)

    if out_path:
        file_console = Console(file=io.StringIO(), record=True, width=120, force_terminal=False, color_system=None)
        file_console.print(table)
        out_path.write_text(file_console.export_text(), encoding="utf-8")
        console.print(f"[bold green]✓ Statistics saved to:[/] {out_path}")
    else:
        console.print(table)
    return records


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
    base_url, clean_host, _scheme, _subpath = parse_server_url(host)
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
                        if cdata.get("query") == query and cdata.get("since_date") == since_date:
                            cached_studies = cdata.get("studies", {})
                            cached_count = len(cached_studies)
                            is_complete = cdata.get("is_complete", False)
                            raw_total_server = cdata.get("total")
                            total_server: int | None = None
                            if raw_total_server is not None:
                                try:
                                    total_server = int(raw_total_server)
                                except (ValueError, TypeError):
                                    total_server = None

                            if limit:
                                if cached_count >= limit:
                                    return dict(list(cached_studies.items())[:limit])
                                if is_complete or (total_server is not None and cached_count >= total_server):
                                    return cached_studies
                                # Partial cache with fewer studies than requested limit -> fetch live from server
                            else:
                                if is_complete or (total_server is not None and cached_count >= total_server):
                                    return cached_studies
        except Exception:
            pass

    active_studies: dict[str, dict[str, Any]] = {}
    server_total: int | None = None

    if target_idno:
        # Directly request target study
        clean_idno = target_idno.strip()
        study_numeric_id = clean_idno
        try:
            search_url = f"{base_url}/index.php/api/catalog/search?sk={urllib.parse.quote(clean_idno)}"
            r_search = requests.get(search_url, headers=headers, timeout=15)
            if r_search.status_code == 200:
                rows = r_search.json().get("result", {}).get("rows", [])
                for row in rows:
                    if row.get("idno") == clean_idno and row.get("id"):
                        study_numeric_id = str(row.get("id"))
                        break
        except Exception:
            pass

        active_studies[clean_idno] = {
            "idno": clean_idno,
            "id": study_numeric_id,
            "title": clean_idno,
            "changed": None,
        }
        return active_studies

    # Discover studies using Catalog Search API
    page = 1
    per_page = 100

    while True:
        url = f"{base_url}/index.php/api/catalog/search?ps={per_page}&page={page}"
        if query:
            url += f"&sk={urllib.parse.quote(query)}"
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
            raw_total = res.get("total", len(rows))
            try:
                total = int(raw_total)
            except (ValueError, TypeError):
                total = len(rows)
            server_total = total

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
    # Fallback for NADA v4 servers (which do not expose the v5 JSON search API)
    if not active_studies:
        try:
            v4_csv_url = f"{base_url}/index.php/catalog/export/csv?ps=5000"
            r_v4_csv = requests.get(v4_csv_url, headers=headers, timeout=25)
            if r_v4_csv.status_code == 200:
                csv_lines = r_v4_csv.text.splitlines()
                header_idx = -1
                for idx, line in enumerate(csv_lines):
                    clean_line = line.strip().lstrip("\ufeff").lower()
                    if clean_line.startswith("id,surveyid") or clean_line.startswith('"id","surveyid"'):
                        header_idx = idx
                        break
                if header_idx != -1:
                    import csv as _csv
                    import io as _io

                    reader = _csv.DictReader(_io.StringIO("\n".join(csv_lines[header_idx:])))
                    for row in reader:
                        survey_id = row.get("surveyid") or row.get("id")
                        if not survey_id:
                            continue
                        survey_id = survey_id.strip()
                        active_studies[survey_id] = {
                            "idno": survey_id,
                            "id": str(row.get("id", survey_id)),
                            "title": row.get("titl") or row.get("title") or survey_id,
                            "nation": row.get("nation", ""),
                            "created": row.get("created"),
                            "changed": row.get("changed") or row.get("created"),
                            "form_model": "survey",
                        }
                        if limit and len(active_studies) >= limit:
                            break
                    if catalog_progress_callback and active_studies:
                        catalog_progress_callback(len(active_studies), len(active_studies))
        except Exception as e:
            file_logger.log(f"[API] v4 CSV fallback error: {e}", level="ERROR")

    # Write to local cache
    if cache_file and active_studies:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            merged_studies = dict(active_studies)
            if cache_file.exists():
                try:
                    with open(cache_file, encoding="utf-8") as f_prev:
                        prev_cdata = json.load(f_prev)
                        if prev_cdata.get("query") == query and prev_cdata.get("since_date") == since_date:
                            prev_studies = prev_cdata.get("studies", {})
                            for k, v in prev_studies.items():
                                if k not in merged_studies:
                                    merged_studies[k] = v
                            if server_total is None and prev_cdata.get("total") is not None:
                                try:
                                    server_total = int(prev_cdata.get("total"))
                                except (ValueError, TypeError):
                                    pass
                except Exception:
                    pass

            is_complete = bool(server_total is not None and len(merged_studies) >= server_total)
            cache_data = {
                "server": clean_host,
                "cached_at": datetime.now(UTC).isoformat(),
                "query": query,
                "since_date": since_date,
                "count": len(merged_studies),
                "total": server_total,
                "is_complete": is_complete,
                "studies": merged_studies,
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
    study_id: str | None = None,
) -> tuple[bytes | None, str, str | None]:
    """Fetch study metadata in the specified format (ddi, json, resources, croissant, schema).
    Returns (content_bytes, file_extension, error_reason).
    """
    base_url, clean_host, _scheme, _subpath = parse_server_url(host)
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
    elif fmt == "croissant":
        url = f"{base_url}/index.php/api/catalog/croissant/{encoded_idno}"
        ext = ".croissant.json"
    elif fmt in ("schema", "schema.org", "json-ld"):
        ext = ".schema.jsonld"
        candidate_urls = [
            f"{base_url}/index.php/catalog/study/{encoded_idno}",
        ]
        if study_id and str(study_id).strip() != encoded_idno:
            candidate_urls.append(f"{base_url}/index.php/catalog/{urllib.parse.quote(str(study_id).strip())}")
        candidate_urls.append(f"{base_url}/index.php/catalog/{encoded_idno}")

        html_text = None
        for cand_url in candidate_urls:
            if verbose:
                console.print(f"[dim]  [API] GET {cand_url}[/dim]")
            file_logger.log(f"[API] GET {cand_url}")
            try:
                r_cand = requests.get(cand_url, headers=headers, timeout=25)
                if r_cand.status_code == 200 and "application/ld+json" in r_cand.text:
                    html_text = r_cand.text
                    break
            except Exception:
                pass

        if html_text:
            m = re.search(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html_text,
                re.DOTALL | re.IGNORECASE,
            )
            if m:
                try:
                    parsed_json = json.loads(m.group(1).strip())
                    content_bytes = json.dumps(parsed_json, indent=2, ensure_ascii=False).encode("utf-8")
                    return content_bytes, ext, None
                except Exception as e:
                    return None, ext, f"Invalid JSON-LD in Schema.org script block: {e}"
            return None, ext, "No application/ld+json script block found on study landing page"
        return None, ext, "Study landing page not found or does not contain Schema.org JSON-LD"
    else:
        return None, f".{fmt}", f"Unsupported format '{fmt}'"

    if verbose:
        console.print(f"[dim]  [API] GET {url}[/dim]")
    file_logger.log(f"[API] GET {url}")

    try:
        r = requests.get(url, headers=headers, timeout=25)
        if r.status_code == 200:
            content = r.content
            if content and len(content.strip()) > 0 and not content.strip().startswith(b'{"status":"failed"'):
                return content, ext, None
            if not content or len(content.strip()) == 0:
                err_msg = "Empty metadata response from server (0 bytes)"
            else:
                err_msg = _extract_http_error_reason(r)
        else:
            err_msg = _extract_http_error_reason(r)

        # Fallback to NADA metadata export endpoint if study_id is available
        # (Crucial when IDNO contains slashes or special characters that break CodeIgniter REST URL routing)
        if study_id:
            fallback_url = None
            if fmt in ("ddi", "xml", "ddi-c"):
                fallback_url = f"{base_url}/index.php/metadata/export/{study_id}/ddi"
            elif fmt in ("json", "metadata"):
                fallback_url = f"{base_url}/index.php/metadata/export/{study_id}/json"

            if fallback_url:
                if verbose:
                    console.print(f"[dim]  [API] GET (fallback) {fallback_url}[/dim]")
                file_logger.log(f"[API] GET (fallback) {fallback_url}")
                try:
                    r_fb = requests.get(fallback_url, headers=headers, timeout=25)
                    if (
                        r_fb.status_code == 200
                        and r_fb.content
                        and len(r_fb.content.strip()) > 0
                        and r_fb.text.strip() != "false"
                        and not r_fb.text.strip().startswith('{"status":"failed"')
                    ):
                        return r_fb.content, ext, None
                except Exception:
                    pass

        return None, ext, err_msg
    except Exception as e:
        if study_id:
            fallback_url = None
            if fmt in ("ddi", "xml", "ddi-c"):
                fallback_url = f"{base_url}/index.php/metadata/export/{study_id}/ddi"
            elif fmt in ("json", "metadata"):
                fallback_url = f"{base_url}/index.php/metadata/export/{study_id}/json"

            if fallback_url:
                try:
                    r_fb = requests.get(fallback_url, headers=headers, timeout=25)
                    if (
                        r_fb.status_code == 200
                        and r_fb.content
                        and len(r_fb.content.strip()) > 0
                        and r_fb.text.strip() != "false"
                        and not r_fb.text.strip().startswith('{"status":"failed"')
                    ):
                        return r_fb.content, ext, None
                except Exception:
                    pass
        return None, ext, str(e)


class ServerHarvester:
    """Manages harvesting, diff detection, and file persistence for a single NADA server."""

    def __init__(self, host: str, repo_root: Path):
        self.host = host.replace("https://", "").replace("http://", "").strip("/")
        if "/" in self.host:
            self.host = self.host.split("/", 1)[0]
        self.dir_name = get_server_dir_name(self.host)
        # Migrate existing directory with raw colon port name if present
        if ":" in self.host:
            raw_dir = repo_root / self.host
            clean_dir = repo_root / self.dir_name
            if raw_dir.exists() and not clean_dir.exists():
                try:
                    raw_dir.rename(clean_dir)
                except Exception:
                    pass
        self.server_dir = repo_root / self.dir_name
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
        workers: int = 4,
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
        work_items = [(idno, fmt, meta) for idno, meta in active_studies.items() for fmt in target_formats]
        completed_count = 0
        manifest_lock = threading.Lock()

        def _notify_progress(pid: str, fmt_str: str, meta_dict: dict[str, Any], fname: str) -> None:
            nonlocal completed_count
            completed_count += 1
            if progress_callback:
                m_with_file = dict(meta_dict)
                m_with_file["filename"] = fname
                try:
                    progress_callback(completed_count, total_items, pid, fmt_str, m_with_file, filename=fname)
                except TypeError:
                    progress_callback(completed_count, total_items, pid, fmt_str, m_with_file)

        def _process_item(item: tuple[str, str, dict[str, Any]]) -> None:
            idno, fmt, meta = item
            rec_key = f"{idno}::{fmt}"
            ext = get_format_extension(fmt)
            file_path, rel_path = get_dataset_metadata_path(self.server_dir, idno, ext=ext)
            fname = rel_path

            # Check non-recoverable error in manifest
            with manifest_lock:
                manifest_errors = self.manifest.get("errors", {})
                if rec_key in manifest_errors and not retry_errors and not target_idno:
                    cached_err_entry = manifest_errors[rec_key]
                    cached_err = cached_err_entry.get("reason", "Non-recoverable error")
                    # Only skip if the error is non-recoverable/permanent; transient errors are auto-retried
                    if is_non_recoverable_error(cached_err) or cached_err_entry.get("recoverable") is False:
                        stats["errors"].append(
                            {
                                "key": rec_key,
                                "pid": idno,
                                "name": meta.get("title"),
                                "format": fmt,
                                "reason": f"[Manifest Cached Error] {cached_err}",
                            }
                        )
                        _notify_progress(idno, fmt, meta, fname)
                        return

            with manifest_lock:
                is_new = rec_key not in existing_keys or not file_path.exists()

                # Fast Timestamp Check
                meta_changed = meta.get("changed") or meta.get("created")
                prev_record = manifest_records.get(rec_key, {})
                prev_changed = prev_record.get("updated_at")

                if not is_new and meta_changed and prev_changed and meta_changed == prev_changed and not force_verify:
                    stats["unchanged"].append(rec_key)
                    _notify_progress(idno, fmt, meta, fname)
                    return

                action = "added" if is_new else "updated"

                if dry_run:
                    stats[action].append(rec_key)
                    _notify_progress(idno, fmt, meta, fname)
                    return

            # Fetch record (unlocked network I/O)
            content_bytes, _, err_msg = fetch_study_record(
                self.host,
                idno,
                metadata_format=fmt,
                verbose=verbose,
                api_token=api_token,
                study_id=meta.get("id"),
            )

            with manifest_lock:
                if content_bytes:
                    content_hash = hashlib.sha256(content_bytes).hexdigest()
                    prev_hash = manifest_records.get(rec_key, {}).get("sha256")
                    if not is_new and prev_hash == content_hash:
                        stats["unchanged"].append(rec_key)
                        if rec_key in self.manifest.get("records", {}):
                            self.manifest["records"][rec_key]["updated_at"] = meta_changed
                    else:
                        file_path.parent.mkdir(parents=True, exist_ok=True)
                        with open(file_path, "wb") as f:
                            f.write(content_bytes)

                        self.manifest.setdefault("records", {})[rec_key] = {
                            "global_id": idno,
                            "path": rel_path,
                            "filename": file_path.name,
                            "format": fmt,
                            "sha256": content_hash,
                            "updated_at": meta_changed,
                            "synced_at": datetime.now(UTC).isoformat(),
                        }
                        if rec_key in self.manifest.get("errors", {}):
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
                    self.manifest.setdefault("errors", {})[rec_key] = {
                        "pid": idno,
                        "format": fmt,
                        "reason": err_msg or "Unknown error",
                        "failed_at": datetime.now(UTC).isoformat(),
                        "recoverable": not is_non_recoverable_error(err_msg),
                    }

                _notify_progress(idno, fmt, meta, fname)

        effective_workers = max(1, workers)
        if effective_workers <= 1 or len(work_items) <= 1:
            for item in work_items:
                _process_item(item)
        else:
            with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                list(executor.map(_process_item, work_items))

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
    retry_errors: Annotated[
        bool,
        typer.Option(
            "--retry-errors",
            "--retry",
            help="Retry downloading studies that previously encountered errors.",
        ),
    ] = False,
    workers: Annotated[
        int,
        typer.Option(
            "--workers",
            "-w",
            help="Number of concurrent worker threads for harvesting (1 for sequential).",
        ),
    ] = 4,
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
            f"Servers: {len(installations)} | Formats: {formats} | Limit: {limit or 'Unlimited'} | Workers: {workers}\n"
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

    total_harvest_errors = 0
    for inst in installations:
        host = inst.clean_hostname
        harvester = ServerHarvester(host, base_repo)

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.fields[server]}[/bold cyan]"),
            BarColumn(bar_width=20),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TextColumn("[dim]{task.fields[filename]}[/dim]"),
            console=console,
        ) as prog:
            task = prog.add_task(
                f"Syncing {host}...",
                total=None,
                server=host,
                filename="Fetching catalog...",
            )

            sync_started = False

            def _truncate_filename(name: str, max_len: int = 35) -> str:
                if len(name) <= max_len:
                    return name
                half = (max_len - 1) // 2
                return name[:half] + "…" + name[-(max_len - 1 - half) :]

            def update_progress(cur, total, pid, fmt, meta, filename=None, t_id=task):
                nonlocal sync_started
                clean_total: int | None = None
                if total is not None:
                    try:
                        clean_total = int(total)
                    except (ValueError, TypeError):
                        clean_total = None
                if not sync_started:
                    prog.reset(t_id, total=clean_total, completed=0)
                    sync_started = True
                raw_fname = filename or meta.get("filename") or f"{pid} ({fmt})"
                prog.update(t_id, total=clean_total, completed=cur, filename=_truncate_filename(raw_fname))

            def update_cat(cur, total, t_id=task):
                clean_total: int | None = None
                if total is not None:
                    try:
                        clean_total = int(total)
                    except (ValueError, TypeError):
                        clean_total = None
                prog.update(t_id, total=clean_total, completed=cur, filename=f"Catalog ({cur}/{clean_total})")

            results = harvester.sync(
                query=query,
                since_date=since_date,
                dry_run=dry_run,
                limit=limit,
                metadata_formats=formats,
                verbose=verbose,
                target_idno=study_id,
                refresh_catalog=refresh,
                retry_errors=retry_errors,
                api_token=api_token,
                progress_callback=update_progress,
                catalog_progress_callback=update_cat,
                workers=workers,
            )

            if not sync_started:
                prog.update(task, completed=0, total=0, filename="No active studies")
            else:
                prog.update(task, filename="Complete")

        err_count = len(results.get("errors", []))
        total_harvest_errors += err_count
        summary_table.add_row(
            host,
            str(len(results.get("datasets", []))),
            str(len(results.get("added", []))),
            str(len(results.get("updated", []))),
            str(len(results.get("unchanged", []))),
            str(err_count),
        )

    console.print(summary_table)

    if total_harvest_errors > 0:
        console.print(
            "\n[yellow]⚠️ Encountered harvest errors on some records. "
            "Run [bold]dartfx-nada errors --details[/bold] to inspect failure reasons.[/yellow]"
        )


if __name__ == "__main__":
    app()
