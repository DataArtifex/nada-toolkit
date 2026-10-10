import json
import re
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from dartfx.nada.cli import app
from dartfx.nada.harvester import (
    ServerHarvester,
    analyze_harvest_errors,
    classify_harvest_error,
    fetch_server_stats,
    format_dataset_url,
    format_response_latency,
    format_version,
    get_dataset_metadata_path,
    is_non_recoverable_error,
    normalize_formats,
    resolve_server_token,
    sanitize_pid,
    save_server_token,
)


def test_sanitize_pid() -> None:
    assert sanitize_pid("MWI_2026_IHS") == "MWI_2026_IHS"
    assert sanitize_pid("MWI/2026/IHS") == "MWI_2026_IHS"
    assert sanitize_pid("TON_ 2015_HIES") == "TON__2015_HIES"


def test_format_version() -> None:
    assert format_version(5) == "v5"
    assert format_version("5.0") == "v5.0"
    assert format_version("v4.2.1") == "v4.2.1"
    assert format_version(None) == "-"


def test_normalize_formats() -> None:
    assert normalize_formats("ddi") == ["ddi"]
    assert normalize_formats("ddi,json,resources") == ["ddi", "json", "resources"]
    assert normalize_formats("all") == ["ddi", "json", "resources"]
    assert normalize_formats("xml") == ["ddi"]
    assert normalize_formats("metadata") == ["json"]
    assert normalize_formats(None) == []


def test_get_dataset_metadata_path(tmp_path: Path) -> None:
    server_dir = tmp_path / "microdata.worldbank.org"
    meta_path, rel_path = get_dataset_metadata_path(server_dir, "WB_2026_01", ".ddi.xml")
    assert rel_path == "WB_2026_01/metadata/ddi.xml"
    assert meta_path == server_dir / "WB_2026_01" / "metadata" / "ddi.xml"


def test_is_non_recoverable_error() -> None:
    assert is_non_recoverable_error("HTTP 404: Not Found") is True
    assert is_non_recoverable_error("Unsupported format") is True
    assert is_non_recoverable_error("HTTP 403: Forbidden") is True
    assert is_non_recoverable_error("Connection timed out") is False
    assert is_non_recoverable_error("HTTP 500") is False


def test_token_persistence_and_resolution(tmp_path: Path, monkeypatch) -> None:
    test_host = "nada.ubos.org"

    # 1. No token initially
    assert resolve_server_token(test_host, repo_root=tmp_path) is None

    # 2. Save token
    save_server_token(test_host, "test-token-secret-123", repo_root=tmp_path)

    # 3. Resolve from server directory and central json
    assert resolve_server_token(test_host, repo_root=tmp_path) == "test-token-secret-123"

    # 4. Explicit token override
    assert resolve_server_token(test_host, repo_root=tmp_path, explicit_token="override-token") == "override-token"

    # 5. Environment variable resolution
    monkeypatch.setenv("NADA_API_TOKEN_NADA_UBOS_ORG", "env-token-xyz")
    assert resolve_server_token(test_host, repo_root=tmp_path / "empty") == "env-token-xyz"


def test_server_harvester_manifest(tmp_path: Path) -> None:
    harvester = ServerHarvester("test.nada.org", tmp_path)
    assert harvester.manifest_file.exists() is False

    # Save dummy record
    harvester.manifest["records"]["WB-001::ddi"] = {
        "global_id": "WB-001",
        "format": "ddi",
        "sha256": "abcd1234efgh",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    harvester._save_manifest()

    # Reload and verify
    reloaded = ServerHarvester("test.nada.org", tmp_path)
    assert "WB-001::ddi" in reloaded.manifest["records"]


def test_classify_harvest_error() -> None:
    assert classify_harvest_error("Unsupported format 'unknown'") == "Exporter Not Supported on Server"
    assert classify_harvest_error("HTTP 401: Unauthorized") == "HTTP 401: Authentication Required (API Token)"
    assert classify_harvest_error("HTTP 403: Restricted") == "HTTP 403: Restricted Study (Authentication Required)"
    assert classify_harvest_error("HTTP 403: Cloudflare Challenge") == "HTTP 403: Forbidden / Bot Protection (WAF)"
    assert classify_harvest_error("HTTP 403: Forbidden") == "HTTP 403: Forbidden"
    assert classify_harvest_error("HTTP 404: Not Found") == "HTTP 404: Study / Endpoint Not Found"
    assert classify_harvest_error("HTTP 410: Deaccessioned") == "HTTP 410: Study Deaccessioned"
    assert classify_harvest_error("HTTP 500: Server Error") == "HTTP 5xx: Server / Upstream Error"
    assert classify_harvest_error("Connection timed out (ReadTimeout)") == "Network: Request Timeout"
    assert classify_harvest_error("Connection refused") == "Network: Connection Failure"
    assert classify_harvest_error("xml.etree.ElementTree.ParseError") == "Parse Error: Malformed XML / JSON"
    assert (
        classify_harvest_error("certificate verify failed: unable to get local issuer certificate")
        == "Network: SSL Incomplete Chain (Missing Intermediate CA)"
    )
    assert (
        classify_harvest_error("certificate verify failed: certificate has expired")
        == "Network: SSL Certificate Expired"
    )
    assert (
        classify_harvest_error("Hostname mismatch: certificate is not valid for 'nada.org'")
        == "Network: SSL Hostname Mismatch"
    )
    assert (
        classify_harvest_error("certificate verify failed: self-signed certificate")
        == "Network: SSL Self-Signed Certificate"
    )
    assert classify_harvest_error("Some random error") == "Other Error"
    assert classify_harvest_error(None) == "Unknown Error"


def test_format_response_latency() -> None:
    assert format_response_latency(None) == "-"
    fast_str = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", format_response_latency(1.2))
    assert "⚡" in fast_str
    assert "1.2s" in fast_str

    normal_str = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", format_response_latency(4.5))
    assert "🟢" in normal_str
    assert "4.5s" in normal_str

    mod_str = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", format_response_latency(9.0))
    assert "🟡" in mod_str
    assert "9.0s" in mod_str

    slow_str = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", format_response_latency(20.0))
    assert "🔴" in slow_str
    assert "20.0s" in slow_str

    err_str = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", format_response_latency(0.1, is_error=True))
    assert "❌" in err_str

    token_str = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", format_response_latency(0.5, requires_token=True))
    assert "🔒" in token_str


def test_fetch_server_stats_caching(tmp_path: Path) -> None:
    server = "cached.nada.org"
    server_dir = tmp_path / server
    server_dir.mkdir(parents=True)
    cache_file = server_dir / ".stats_cache.json"

    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "server": server,
                "cached_at": datetime.now(UTC).isoformat(),
                "studies": 350,
                "collections": 12,
                "is_nada": True,
                "version": "5",
                "error": None,
            },
            f,
        )

    res = fetch_server_stats(server, repo_root=tmp_path, cache_ttl_hours=100.0)
    assert res.get("cached") is True
    assert res.get("studies") == 350
    assert res.get("collections") == 12
    assert res.get("version") == "5"


def test_analyze_harvest_errors(tmp_path: Path) -> None:
    # 1. Empty dir
    empty_analysis = analyze_harvest_errors(tmp_path)
    assert empty_analysis["total_errors"] == 0
    assert empty_analysis["total_datasets"] == 0

    # 2. Add manifest with error
    server_dir = tmp_path / "microdata.worldbank.org"
    server_dir.mkdir(parents=True)
    manifest = {
        "server": "microdata.worldbank.org",
        "records": {},
        "errors": {
            "WB-001::ddi": {
                "pid": "WB-001",
                "format": "ddi",
                "reason": "HTTP 404: Not Found",
                "failed_at": "2026-01-01T00:00:00Z",
            }
        },
    }
    with open(server_dir / ".manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f)

    analysis = analyze_harvest_errors(tmp_path)
    assert analysis["total_errors"] == 1
    assert analysis["total_datasets"] == 1
    assert "HTTP 404: Study / Endpoint Not Found" in analysis["by_type"]


def test_format_dataset_url() -> None:
    url = format_dataset_url("WB-001", host="microdata.worldbank.org")
    assert url == "https://microdata.worldbank.org/index.php/catalog/WB-001"


def test_server_harvester_sync_dry_run(monkeypatch, tmp_path: Path) -> None:
    mock_studies = {
        "WB-101": {
            "idno": "WB-101",
            "id": "1",
            "title": "Dry Run Study",
            "changed": "2026-01-01",
        }
    }

    monkeypatch.setattr(
        "dartfx.nada.harvester.fetch_active_studies",
        lambda *_args, **_kwargs: mock_studies,
    )

    harvester = ServerHarvester("microdata.worldbank.org", tmp_path)
    res = harvester.sync(dry_run=True, limit=1, metadata_formats="ddi")
    assert len(res["added"]) == 1
    assert "WB-101::ddi" in res["added"]


def test_cli_harvest_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["harvest", "--help"])
    assert result.exit_code == 0
    assert "--server" in result.stdout
    assert "--dry-run" in result.stdout
    assert "--limit" in result.stdout
