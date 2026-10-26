import json
import re
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import requests
from typer.testing import CliRunner

from dartfx.nada.cli import app
from dartfx.nada.harvester import (
    ServerHarvester,
    _is_parked_or_domain_sale,
    abbreviate_error,
    analyze_harvest_errors,
    classify_harvest_error,
    extract_server_environment,
    fetch_active_studies,
    fetch_server_stats,
    format_dataset_url,
    format_php_version,
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
    assert sanitize_pid("UBOS-BR-2006/07-v01") == "UBOS-BR-2006_07-v01"
    assert sanitize_pid("UGA-UBOS-UCA-2008/09") == "UGA-UBOS-UCA-2008_09"


def test_format_version() -> None:
    assert format_version(5) == "v5"
    assert format_version("5.0") == "v5.0"
    assert format_version("v4.2.1") == "v4.2.1"
    assert format_version(None) == "-"


def test_normalize_formats() -> None:
    assert normalize_formats("ddi") == ["ddi"]
    assert normalize_formats("ddi,json,resources") == ["ddi", "json", "resources"]
    assert normalize_formats("all") == ["ddi", "json", "resources", "croissant", "schema"]
    assert normalize_formats("xml") == ["ddi"]
    assert normalize_formats("metadata") == ["json"]
    assert normalize_formats("schema") == ["schema"]
    assert normalize_formats("schema.org") == ["schema"]
    assert normalize_formats(None) == []


def test_get_dataset_metadata_path(tmp_path: Path) -> None:
    server_dir = tmp_path / "microdata.worldbank.org"
    meta_path, rel_path = get_dataset_metadata_path(server_dir, "WB_2026_01", ".ddi.xml")
    assert rel_path == "WB_2026_01/metadata/ddi.xml"
    assert meta_path == server_dir / "WB_2026_01" / "metadata" / "ddi.xml"

    # Identifiers containing slashes
    meta_path_slash, rel_path_slash = get_dataset_metadata_path(server_dir, "UBOS-BR-2006/07-v01", ".ddi.xml")
    assert rel_path_slash == "UBOS-BR-2006_07-v01/metadata/ddi.xml"
    assert meta_path_slash == server_dir / "UBOS-BR-2006_07-v01" / "metadata" / "ddi.xml"


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
    wrapped_ssl_err = (
        "HTTPSConnectionPool(host='microdata.cbs.gov.np', port=443): Max retries exceeded "
        "with url: /index.php/api/catalog/collections (Caused by SSLError(SSLCertVerificationError(1, "
        '"[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, '
        "certificate is not valid for 'microdata.cbs.gov.np'. (_ssl.c:1010)\")))"
    )
    assert classify_harvest_error(wrapped_ssl_err) == "Network: SSL Hostname Mismatch"
    assert classify_harvest_error("Some random error") == "Other Error"
    assert classify_harvest_error(None) == "Unknown Error"


def test_abbreviate_error() -> None:
    wrapped_ssl_err = (
        "HTTPSConnectionPool(host='microdata.cbs.gov.np', port=443): Max retries exceeded "
        "with url: /index.php/api/catalog/collections (Caused by SSLError(SSLCertVerificationError(1, "
        '"[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, '
        "certificate is not valid for 'microdata.cbs.gov.np'. (_ssl.c:1010)\")))"
    )
    assert abbreviate_error(wrapped_ssl_err) == "SSL Hostname Mismatch"
    assert abbreviate_error("Timeout (10s)") == "Timeout (10s)"
    assert abbreviate_error("Connection timed out (ReadTimeout)") == "Request Timeout"
    assert abbreviate_error("Connection refused") == "Connection Refused"
    assert abbreviate_error("certificate has expired") == "SSL Certificate Expired"
    assert abbreviate_error("unable to get local issuer certificate") == "SSL Incomplete Chain"
    assert abbreviate_error("self-signed certificate") == "SSL Self-Signed Cert"
    assert abbreviate_error("HTTP 401: Unauthorized") == "Auth Required (401)"
    assert abbreviate_error("HTTP 403: Forbidden") == "Forbidden (403)"
    assert abbreviate_error("Cloudflare challenge") == "Cloudflare Challenge"
    assert abbreviate_error("AWS WAF block") == "Bot Protection (WAF)"
    assert abbreviate_error("Perimeter URL Block") == "Perimeter Block (Geo/IP)"
    assert abbreviate_error("HTTP 410: Catalog Retired") == "Catalog Retired (410)"
    assert abbreviate_error("HTTP 503: Service Unavailable") == "Service Unavailable (503)"
    assert abbreviate_error("HTTP 404: Not Found") == "Not Found (404)"
    assert abbreviate_error("HTTP 500: Server Error") == "Server Error (500)"
    assert abbreviate_error("HTTP 502: Bad Gateway") == "Bad Gateway (502)"
    assert abbreviate_error("HugeDomains.com is for sale") == "Domain For Sale"
    assert abbreviate_error("Not a NADA server") == "Not NADA Server"
    assert abbreviate_error(None) == "Unknown Error"


def test_is_parked_or_domain_sale() -> None:
    import requests

    resp_parked = requests.Response()
    resp_parked.url = "https://www.hugedomains.com/domain_profile.cfm?d=microdatahub.com"
    resp_parked.status_code = 200
    assert _is_parked_or_domain_sale("botswana.microdatahub.com", resp_parked) is True

    resp_valid = requests.Response()
    resp_valid.url = "https://microdata.worldbank.org/index.php/api/catalog/collections"
    resp_valid.status_code = 200
    assert _is_parked_or_domain_sale("microdata.worldbank.org", resp_valid) is False


def test_format_response_latency() -> None:
    assert format_response_latency(None) == "-"
    fast_str = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", format_response_latency(1.2))
    assert "🟢⚡" in fast_str
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


def test_fetch_server_stats_env_var_repo(tmp_path: Path, monkeypatch) -> None:
    server = "env-cached.nada.org"
    server_dir = tmp_path / server
    server_dir.mkdir(parents=True)
    cache_file = server_dir / ".stats_cache.json"

    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "server": server,
                "cached_at": datetime.now(UTC).isoformat(),
                "studies": 123,
                "collections": 5,
                "is_nada": True,
                "version": "5",
                "error": None,
            },
            f,
        )

    # 1. Test DARTFX_NADA_REPOSITORY
    monkeypatch.setenv("DARTFX_NADA_REPOSITORY", str(tmp_path))
    monkeypatch.delenv("DARTFX_NADA_REPOSITORY_ROOT", raising=False)
    res = fetch_server_stats(server, cache_ttl_hours=100.0)
    assert res.get("cached") is True
    assert res.get("studies") == 123

    # 2. Test DARTFX_NADA_REPOSITORY_ROOT fallback
    monkeypatch.delenv("DARTFX_NADA_REPOSITORY", raising=False)
    monkeypatch.setenv("DARTFX_NADA_REPOSITORY_ROOT", str(tmp_path))
    res2 = fetch_server_stats(server, cache_ttl_hours=100.0)
    assert res2.get("cached") is True
    assert res2.get("studies") == 123


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
    clean_output = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", result.stdout)
    assert "--server" in clean_output
    assert "--dry-run" in clean_output
    assert "--limit" in clean_output
    assert "--workers" in clean_output
    assert "-w" in clean_output


def test_fetch_server_stats_v4_study_count(monkeypatch) -> None:
    def mock_get(url, **_kwargs):
        resp = MagicMock()
        if "api/catalog/collections" in url:
            resp.status_code = 404
        elif "api/catalog/search" in url:
            resp.status_code = 404
        elif "catalog/export/csv" in url:
            resp.status_code = 200
            resp.text = "id,surveyid,titl\n1,TEST,Sample"
        elif "catalog" in url:
            resp.status_code = 200
            resp.text = '<div class="search-count">Trouvé <b>163</b> études parmis <b>163</b></div>'
        return resp

    monkeypatch.setattr("requests.get", mock_get)
    stats = fetch_server_stats("mock.stat.cm", refresh_cache=True)
    assert stats["is_nada"] is True
    assert stats["version"] == "4"
    assert stats["studies"] == 163


def test_fetch_server_stats_ssl_fallback_to_http(monkeypatch) -> None:
    def mock_get(url, **_kwargs):
        if url.startswith("https://"):
            msg = "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate"
            raise requests.exceptions.SSLError(msg)
        # HTTP succeeds
        resp = MagicMock()
        if "api/catalog/search" in url:
            resp.status_code = 200
            resp.json = MagicMock(return_value={"result": {"total": 121, "rows": []}})
        else:
            resp.status_code = 404
        return resp

    monkeypatch.setattr("requests.get", mock_get)
    stats = fetch_server_stats("mock-ssl.nada.org", refresh_cache=True)
    assert stats["is_nada"] is True
    assert stats["http_fallback"] is True
    assert stats["ssl_error"] == "SSL Incomplete Chain"
    assert stats["studies"] == 121


def test_fetch_server_stats_http_recommends_https(monkeypatch) -> None:
    def mock_get(url, **_kwargs):
        resp = MagicMock()
        if "api/catalog/search" in url:
            resp.status_code = 200
            resp.json = MagicMock(return_value={"result": {"total": 50, "rows": []}})
        else:
            resp.status_code = 404
        return resp

    monkeypatch.setattr("requests.get", mock_get)
    stats = fetch_server_stats("http://mock-plain.nada.org", refresh_cache=True)
    assert stats["is_nada"] is True
    assert stats["https_upgrade_recommended"] is True
    assert stats["studies"] == 50


def test_fetch_study_record_schema(monkeypatch) -> None:
    from dartfx.nada.harvester import fetch_study_record

    def mock_get(_url, **_kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = """
        <html><head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org/",
          "@type": "Dataset",
          "name": "Test Survey Dataset",
          "identifier": "TEST-1"
        }
        </script>
        </head><body>Content</body></html>
        """
        return resp

    monkeypatch.setattr("requests.get", mock_get)
    content, ext, err = fetch_study_record("mock.nada.org", "TEST-1", metadata_format="schema")
    assert err is None
    assert ext == ".schema.jsonld"
    assert content is not None
    data = json.loads(content.decode("utf-8"))
    assert data["@context"] == "https://schema.org/"
    assert data["name"] == "Test Survey Dataset"


def test_fetch_study_record_slash_idno_fallback(monkeypatch) -> None:
    from dartfx.nada.harvester import fetch_study_record

    calls = []

    def mock_get(url, **_kwargs):
        calls.append(url)
        resp = MagicMock()
        if "api/catalog/ddi" in url:
            # CodeIgniter route fails when IDNO has slashes
            resp.status_code = 400
            resp.text = '{"status":"failed","errors":"IDNO-NOT-FOUND"}'
            resp.content = resp.text.encode("utf-8")
        elif "metadata/export/5/ddi" in url:
            # Export fallback succeeds
            resp.status_code = 200
            resp.text = '<?xml version="1.0"?><codeBook ID="UBOS-BR-2006/07-v01"></codeBook>'
            resp.content = resp.text.encode("utf-8")
        else:
            resp.status_code = 404
            resp.text = "Not found"
            resp.content = b""
        return resp

    monkeypatch.setattr("requests.get", mock_get)

    content, ext, err = fetch_study_record(
        "microdata.ubos.org",
        "UBOS-BR-2006/07-v01",
        metadata_format="ddi",
        study_id="5",
    )
    assert err is None
    assert ext == ".ddi.xml"
    assert content is not None
    assert b"UBOS-BR-2006/07-v01" in content
    assert any("metadata/export/5/ddi" in c for c in calls)


def test_fetch_active_studies_cache_expansion_with_higher_limit(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def mock_get(url, **_kwargs):
        calls.append(url)
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(
            return_value={
                "result": {
                    "total": 10,
                    "rows": [{"idno": f"ST-{i}", "id": str(i), "title": f"Study {i}"} for i in range(1, 11)],
                }
            }
        )
        return resp

    monkeypatch.setattr("requests.get", mock_get)

    # 1. Fetch with limit=2 (should query server and cache 2 studies)
    studies_1 = fetch_active_studies("mock.server.org", limit=2, server_dir=tmp_path)
    assert len(studies_1) == 2
    assert len(calls) == 1

    # 2. Fetch with limit=4 (cached 2 is less than 4, so must bypass cache and query server)
    studies_2 = fetch_active_studies("mock.server.org", limit=4, server_dir=tmp_path)
    assert len(studies_2) == 4
    assert len(calls) == 2

    # 3. Fetch with limit=3 (cached now has 4, which is >= 3, so should hit cache without network call)
    studies_3 = fetch_active_studies("mock.server.org", limit=3, server_dir=tmp_path)
    assert len(studies_3) == 3
    assert len(calls) == 2  # No new network call!


def test_server_harvester_sync_multi_threaded(monkeypatch, tmp_path: Path) -> None:
    mock_studies = {
        f"ST-{i}": {
            "idno": f"ST-{i}",
            "id": str(i),
            "title": f"Study {i}",
            "changed": "2026-01-01",
        }
        for i in range(1, 13)
    }

    monkeypatch.setattr(
        "dartfx.nada.harvester.fetch_active_studies",
        lambda *_args, **_kwargs: mock_studies,
    )

    def mock_fetch_study_record(_host, idno, metadata_format="ddi", **_kwargs):
        return f"<xml>Study {idno} {metadata_format}</xml>".encode(), ".ddi.xml", None

    monkeypatch.setattr(
        "dartfx.nada.harvester.fetch_study_record",
        mock_fetch_study_record,
    )

    progress_calls = []

    def on_progress(cur, total, pid, fmt, meta, filename=None):
        progress_calls.append((cur, total, pid, fmt, filename or meta.get("filename")))

    harvester = ServerHarvester("mock.server.org", tmp_path)
    res = harvester.sync(limit=12, metadata_formats="ddi", workers=4, progress_callback=on_progress)

    assert len(res["added"]) == 12
    assert len(res["errors"]) == 0
    assert len(harvester.manifest["records"]) == 12
    assert len(progress_calls) == 12
    for _cur, tot, pid, _fmt, fname in progress_calls:
        assert tot == 12
        assert fname == f"{pid}/metadata/ddi.xml"

    # Verify files written to disk
    for i in range(1, 13):
        rec_key = f"ST-{i}::ddi"
        assert rec_key in harvester.manifest["records"]
        file_path = tmp_path / "mock.server.org" / harvester.manifest["records"][rec_key]["path"]
        assert file_path.exists()
        assert file_path.read_bytes() == f"<xml>Study ST-{i} ddi</xml>".encode()

    # Second sync run with workers=4 should recognize all as unchanged
    res_second = harvester.sync(limit=12, metadata_formats="ddi", workers=4)
    assert len(res_second["added"]) == 0
    assert len(res_second["unchanged"]) == 12


def test_harvester_records_recoverable_and_non_recoverable_errors(monkeypatch, tmp_path: Path) -> None:
    mock_studies = {
        "ERR-RECOVERABLE": {
            "idno": "ERR-RECOVERABLE",
            "id": "1",
            "title": "Recoverable Study",
        },
        "ERR-PERMANENT": {
            "idno": "ERR-PERMANENT",
            "id": "2",
            "title": "Permanent Error Study",
        },
    }

    monkeypatch.setattr(
        "dartfx.nada.harvester.fetch_active_studies",
        lambda *_args, **_kwargs: mock_studies,
    )

    fetch_attempts = {"ERR-RECOVERABLE": 0, "ERR-PERMANENT": 0}

    def mock_fetch(_host, idno, **_kwargs):
        fetch_attempts[idno] += 1
        if idno == "ERR-RECOVERABLE":
            if fetch_attempts[idno] == 1:
                return None, ".ddi.xml", "Read timed out"
            return b"<xml>Recovered</xml>", ".ddi.xml", None
        return None, ".ddi.xml", "HTTP 404: Not Found"

    monkeypatch.setattr(
        "dartfx.nada.harvester.fetch_study_record",
        mock_fetch,
    )

    harvester = ServerHarvester("mock.server.org", tmp_path)
    res_1 = harvester.sync(metadata_formats="ddi")
    assert len(res_1["errors"]) == 2

    # Verify both errors are saved in manifest
    assert "ERR-RECOVERABLE::ddi" in harvester.manifest["errors"]
    assert harvester.manifest["errors"]["ERR-RECOVERABLE::ddi"]["recoverable"] is True
    assert "ERR-PERMANENT::ddi" in harvester.manifest["errors"]
    assert harvester.manifest["errors"]["ERR-PERMANENT::ddi"]["recoverable"] is False

    # Verify analyze_harvest_errors finds them
    analysis = analyze_harvest_errors(repo_dir=tmp_path)
    assert analysis["total_errors"] == 2
    assert len(analysis["records"]) == 2

    # Second sync: ERR-RECOVERABLE is auto-retried and succeeds; ERR-PERMANENT is skipped as cached error
    res_2 = harvester.sync(metadata_formats="ddi")
    assert len(res_2["added"]) == 1
    assert "ERR-RECOVERABLE::ddi" in res_2["added"]
    assert "ERR-RECOVERABLE::ddi" not in harvester.manifest["errors"]
    assert "ERR-PERMANENT::ddi" in harvester.manifest["errors"]
    assert fetch_attempts["ERR-PERMANENT"] == 1

    # Third sync with retry_errors=True forces retrying cached permanent errors
    res_3 = harvester.sync(metadata_formats="ddi", retry_errors=True)
    assert fetch_attempts["ERR-PERMANENT"] == 2
    assert len(res_3["errors"]) == 1


def test_fetch_active_studies_string_total(monkeypatch) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(
        return_value={
            "result": {
                "total": "71",  # string total returned by some NADA servers (e.g. microdata.ubos.org:7070)
                "rows": [
                    {"idno": "ST-1", "title": "Study 1"},
                    {"idno": "ST-2", "title": "Study 2"},
                ],
            }
        }
    )

    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: mock_resp)

    progress_calls = []

    def on_progress(cur, total):
        progress_calls.append((cur, total))

    studies = fetch_active_studies(
        "microdata.ubos.org:7070",
        limit=2,
        catalog_progress_callback=on_progress,
        refresh_catalog=True,
    )

    assert len(studies) == 2
    assert "ST-1" in studies
    assert "ST-2" in studies
    assert len(progress_calls) >= 1
    cur, total = progress_calls[0]
    assert isinstance(total, int)
    assert total == 2


def test_server_dir_name_excludes_port(tmp_path: Path) -> None:
    from dartfx.nada.harvester import get_server_dir_name

    assert get_server_dir_name("microdata.ubos.org:7070") == "microdata.ubos.org"
    assert get_server_dir_name("https://microdata.ubos.org:7070/index.php") == "microdata.ubos.org"
    assert get_server_dir_name("http://nada.test.org:8080") == "nada.test.org"
    assert get_server_dir_name("microdata.worldbank.org") == "microdata.worldbank.org"

    # ServerHarvester should keep host address with port for network,
    # but cleanly exclude the port from the server directory name on disk
    harvester = ServerHarvester("microdata.ubos.org:7070", tmp_path)
    assert harvester.host == "microdata.ubos.org:7070"
    assert harvester.dir_name == "microdata.ubos.org"
    assert harvester.server_dir == tmp_path / "microdata.ubos.org"
    assert ":" not in harvester.server_dir.name
    assert ":" not in harvester.dir_name


def test_server_harvester_resolves_subpath(tmp_path: Path) -> None:
    from dartfx.nada.harvester import parse_server_url

    # DataFirst is registered with catalog URL https://www.datafirst.uct.ac.za/dataportal
    base_url, clean_host, scheme, subpath = parse_server_url("www.datafirst.uct.ac.za")
    assert clean_host == "www.datafirst.uct.ac.za"
    assert subpath == "/dataportal"
    assert base_url == "https://www.datafirst.uct.ac.za/dataportal"

    # ServerHarvester should keep clean directory name on disk while maintaining full catalog url
    harvester = ServerHarvester("www.datafirst.uct.ac.za", tmp_path)
    assert harvester.host == "www.datafirst.uct.ac.za"
    assert harvester.url == "https://www.datafirst.uct.ac.za/dataportal"
    assert harvester.dir_name == "www.datafirst.uct.ac.za"
    assert harvester.server_dir == tmp_path / "www.datafirst.uct.ac.za"


def test_extract_server_environment() -> None:
    resp = MagicMock()
    resp.headers = {
        "X-Powered-By": "PHP/8.2.14",
        "Server": "Apache/2.4.52 (Ubuntu)",
    }
    php, srv = extract_server_environment(resp)
    assert php == "8.2.14"
    assert srv == "Apache/2.4.52 (Ubuntu)"

    # Server header with inline PHP module
    resp2 = MagicMock()
    resp2.headers = {
        "Server": "Apache/2.4.41 (Ubuntu) PHP/7.4.3",
    }
    php2, srv2 = extract_server_environment(resp2)
    assert php2 == "7.4.3"
    assert srv2 == "Apache/2.4.41 (Ubuntu)"

    # X-PHP-Version header
    resp3 = MagicMock()
    resp3.headers = {
        "X-PHP-Version": "8.1.1",
        "Server": "nginx/1.22.0",
    }
    php3, srv3 = extract_server_environment(resp3)
    assert php3 == "8.1.1"
    assert srv3 == "nginx/1.22.0"

    # No PHP detected (e.g. Cloudflare)
    resp4 = MagicMock()
    resp4.headers = {
        "Server": "cloudflare",
        "X-Powered-By": "ASP.NET",
    }
    php4, srv4 = extract_server_environment(resp4)
    assert php4 is None
    assert srv4 == "cloudflare"

    # None response
    php5, srv5 = extract_server_environment(None)
    assert php5 is None
    assert srv5 is None


def test_format_php_version() -> None:
    assert "bold red" in format_php_version("7.4.33")
    assert "bold red" in format_php_version("5.6.40")
    assert "yellow" in format_php_version("8.0.28")
    assert "yellow" in format_php_version("8.1.2")
    assert "green" in format_php_version("8.2.14")
    assert "green" in format_php_version("8.3.4")

    # Hidden / Unknown handling
    assert "Hidden" in format_php_version(None, nada_ver="5")
    assert "~5.x-7.x" in format_php_version(None, nada_ver="4")
    assert format_php_version(None, is_online=False) == "-"


def test_fetch_server_stats_php_version_detected(monkeypatch, tmp_path: Path) -> None:
    def mock_get(url, **_kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {
            "X-Powered-By": "PHP/8.1.28",
            "Server": "Apache/2.4.58 (Debian)",
        }
        if "api/catalog/search" in url:
            resp.json = MagicMock(return_value={"result": {"total": 88, "rows": []}})
        elif "api/catalog/collections" in url:
            resp.json = MagicMock(return_value={"result": [{"id": 1}]})
        return resp

    monkeypatch.setattr("requests.get", mock_get)
    stats = fetch_server_stats("php-detected.nada.org", repo_root=tmp_path, refresh_cache=True)
    assert stats["is_nada"] is True
    assert stats["studies"] == 88
    assert stats["php_version"] == "8.1.28"
    assert stats["web_server"] == "Apache/2.4.58 (Debian)"

    # Verify cached entry retains environment
    cache_file = tmp_path / "php-detected.nada.org" / ".stats_cache.json"
    assert cache_file.exists()
    cached = json.loads(cache_file.read_text("utf-8"))
    assert cached["php_version"] == "8.1.28"
    assert cached["web_server"] == "Apache/2.4.58 (Debian)"


def test_display_server_stats_sorting(monkeypatch) -> None:
    from dartfx.nada.harvester import display_server_stats
    from dartfx.nada.nada import ServerInstallation

    mock_insts = [
        ServerInstallation(name="Zambia Stats", hostname="b.zamstats.gov.zm", country_code="ZM"),
        ServerInstallation(name="Angola Stats", hostname="a.ine.gov.ao", country_code="AO"),
        ServerInstallation(name="Benin Stats", hostname="c.insae-bj.org", country_code="BJ"),
    ]

    mock_stats = {
        "b.zamstats.gov.zm": {"is_nada": True, "studies": 10, "latency_seconds": 1.2, "version": "5"},
        "a.ine.gov.ao": {"is_nada": True, "studies": 50, "latency_seconds": 4.5, "version": "5"},
        "c.insae-bj.org": {"is_nada": True, "studies": 5, "latency_seconds": 2.1, "version": "4"},
    }

    monkeypatch.setattr("dartfx.nada.harvester.fetch_nada_installations", lambda **_kwargs: mock_insts)
    monkeypatch.setattr(
        "dartfx.nada.harvester.fetch_server_stats",
        lambda host, **_kwargs: next((v for k, v in mock_stats.items() if k in host), {}),
    )

    rendered_rows: list[list[str]] = []

    def mock_add_row(_self, *args, **_kwargs):
        if args and isinstance(args[0], str) and "Stats" in args[0]:
            rendered_rows.append(list(args))

    monkeypatch.setattr("rich.table.Table.add_row", mock_add_row)

    # 1. Default: sort by name (Angola, Benin, Zambia)
    rendered_rows.clear()
    display_server_stats()
    assert len(rendered_rows) == 3
    assert "Angola Stats" in rendered_rows[0][0]
    assert "Benin Stats" in rendered_rows[1][0]
    assert "Zambia Stats" in rendered_rows[2][0]

    # 2. Sort by studies (descending by default: Angola 50, Zambia 10, Benin 5)
    rendered_rows.clear()
    display_server_stats(sort_by="studies")
    assert "Angola Stats" in rendered_rows[0][0]
    assert "Zambia Stats" in rendered_rows[1][0]
    assert "Benin Stats" in rendered_rows[2][0]

    # 3. Sort by latency (fastest first: Zambia 1.2, Benin 2.1, Angola 4.5)
    rendered_rows.clear()
    display_server_stats(sort_by="latency")
    assert "Zambia Stats" in rendered_rows[0][0]
    assert "Benin Stats" in rendered_rows[1][0]
    assert "Angola Stats" in rendered_rows[2][0]

    # 4. Sort by name descending
    rendered_rows.clear()
    display_server_stats(sort_by="name", descending=True)
    assert "Zambia Stats" in rendered_rows[0][0]
    assert "Benin Stats" in rendered_rows[1][0]
    assert "Angola Stats" in rendered_rows[2][0]


def test_display_server_stats_links_full_url(monkeypatch) -> None:
    from dartfx.nada.harvester import display_server_stats
    from dartfx.nada.nada import ServerInstallation

    mock_insts = [
        ServerInstallation(
            name="Togo INSEED",
            url="http://160.242.193.218/nada",
            country_code="TG",
        ),
    ]

    monkeypatch.setattr("dartfx.nada.harvester.fetch_nada_installations", lambda **_kwargs: mock_insts)
    monkeypatch.setattr(
        "dartfx.nada.harvester.fetch_server_stats",
        lambda **_kwargs: {"is_nada": True, "studies": 15, "latency_seconds": 0.5, "version": "5"},
    )

    rendered_rows: list[list[str]] = []

    def mock_add_row(_self, *args, **_kwargs):
        if args and isinstance(args[0], str) and "Togo" in args[0]:
            rendered_rows.append(list(args))

    monkeypatch.setattr("rich.table.Table.add_row", mock_add_row)

    display_server_stats()
    assert len(rendered_rows) == 1
    # Ensure the link retains the full catalog path /nada and does not truncate to hostname
    assert "[link=http://160.242.193.218/nada]Togo INSEED[/link]" in rendered_rows[0][0]


def test_display_server_stats_json_and_markdown(monkeypatch, capsys) -> None:
    from dartfx.nada.harvester import display_server_stats
    from dartfx.nada.nada import ServerInstallation

    mock_insts = [
        ServerInstallation(
            name="Armenia Stats",
            url="https://microdata.armstat.am",
            country_code="AM",
        ),
    ]

    monkeypatch.setattr("dartfx.nada.harvester.fetch_nada_installations", lambda **_kwargs: mock_insts)
    monkeypatch.setattr(
        "dartfx.nada.harvester.fetch_server_stats",
        lambda **_kwargs: {
            "is_nada": True,
            "studies": 26,
            "latency_seconds": 3.6,
            "version": "5",
            "php_version": "7.2.19",
            "web_server": "Apache",
            "error": None,
        },
    )

    # 1. Test JSON output
    records = display_server_stats(format="json")
    captured = capsys.readouterr()
    assert len(records) == 1
    assert records[0]["name"] == "Armenia Stats"
    assert records[0]["url"] == "https://microdata.armstat.am"
    assert records[0]["studies"] == 26
    assert records[0]["status"] == "Online"

    parsed_json = json.loads(captured.out)
    assert len(parsed_json) == 1
    assert parsed_json[0]["country_code"] == "AM"
    assert parsed_json[0]["php_version"] == "7.2.19"

    # 2. Test Markdown output
    records_md = display_server_stats(format="markdown")
    captured_md = capsys.readouterr()
    assert len(records_md) == 1
    assert "| Repository / Server | Country | Ver | PHP | Studies | Latency | Status |" in captured_md.out
    assert "|:---|:---:|:---:|:---:|---:|:---:|:---|" in captured_md.out
    assert "[Armenia Stats](https://microdata.armstat.am)" in captured_md.out
    assert "7.2.19" in captured_md.out
    assert "Online" in captured_md.out

    # 3. Test Markdown output with show_env
    display_server_stats(format="markdown", show_env=True)
    captured_md_env = capsys.readouterr()
    assert "| Web Server |" in captured_md_env.out
    assert "Apache" in captured_md_env.out

    # 4. Test file output
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        json_file = Path(tmp_dir) / "test_stats.json"
        display_server_stats(format="json", output=json_file)
        assert json_file.exists()
        saved_json = json.loads(json_file.read_text("utf-8"))
        assert len(saved_json) == 1
        assert saved_json[0]["name"] == "Armenia Stats"

        md_file = Path(tmp_dir) / "test_stats.md"
        display_server_stats(format="markdown", output=md_file)
        assert md_file.exists()
        assert "| Repository / Server |" in md_file.read_text("utf-8")

        txt_file = Path(tmp_dir) / "test_stats.txt"
        display_server_stats(format="table", output=txt_file)
        assert txt_file.exists()
        assert "Armenia Stats" in txt_file.read_text("utf-8")


def test_user_agent_env_override(monkeypatch) -> None:
    from dartfx.nada.harvester import get_request_headers
    from dartfx.nada.nada import NadaServer

    # 1. Default User-Agent has Mozilla/5.0 compatible prefix
    default_headers = get_request_headers()
    assert "Mozilla/5.0" in default_headers["User-Agent"]
    assert "dartfx-nada" in default_headers["User-Agent"]

    # 2. Environment override
    custom = "CustomResearchBot/1.0 (+https://example.org)"
    monkeypatch.setenv("NADA_USER_AGENT", custom)

    headers = get_request_headers()
    assert headers["User-Agent"] == custom

    server = NadaServer(server="mock.nada.org", api_key="test-key")
    assert server.user_agent == custom


def test_diagnose_probe_error() -> None:
    from dartfx.nada.harvester import _diagnose_probe_error

    # 1. Perimeter URL Block
    r_perimeter = requests.Response()
    r_perimeter.status_code = 403
    r_perimeter._content = b"<html><head><title>Perimeter URL Block</title></head><body>IP Blocked</body></html>"
    assert _diagnose_probe_error([r_perimeter]) == "Perimeter Block (Geo/IP)"

    # 2. Cloudflare Challenge (cf-mitigated or challenges.cloudflare.com)
    r_cf = requests.Response()
    r_cf.status_code = 403
    r_cf.headers["cf-mitigated"] = "challenge"
    r_cf.headers["Server"] = "cloudflare"
    r_cf._content = b"<html><head><title>Just a moment...</title></head><body>Turnstile challenge</body></html>"
    assert _diagnose_probe_error([r_cf]) == "Cloudflare Challenge"

    # 3. Bot Protection (WAF)
    r_waf = requests.Response()
    r_waf.status_code = 403
    r_waf.headers["Server"] = "awselb/2.0"
    r_waf._content = b"<html><body>AWS WAF Access Denied</body></html>"
    assert _diagnose_probe_error([r_waf]) == "Bot Protection (WAF)"

    # 4. Auth Required (401)
    r_auth = requests.Response()
    r_auth.status_code = 401
    r_auth._content = b"Unauthorized"
    assert _diagnose_probe_error([r_auth]) == "Auth Required (401)"

    # 5. Catalog Retired (410)
    r_retired = requests.Response()
    r_retired.status_code = 410
    r_retired._content = b"<html><head><title>410 Gone | ILO Survey Catalog Retired</title></head></html>"
    assert _diagnose_probe_error([r_retired]) == "Catalog Retired (410)"

    # 6. Server / Gateway Errors (5xx)
    r_503 = requests.Response()
    r_503.status_code = 503
    r_503._content = b"Service Unavailable"
    assert _diagnose_probe_error([r_503]) == "Service Unavailable (503)"

    r_502 = requests.Response()
    r_502.status_code = 502
    r_502._content = b"Bad Gateway"
    assert _diagnose_probe_error([r_502]) == "Bad Gateway (502)"

    r_500 = requests.Response()
    r_500.status_code = 500
    r_500._content = b"Internal Server Error"
    assert _diagnose_probe_error([r_500]) == "Server Error (500)"

    # 7. PHP Fatal Error on 200
    r_php = requests.Response()
    r_php.status_code = 200
    r_php._content = b"<br /><b>Fatal error</b>: Uncaught Error in /home/nada/index.php"
    assert _diagnose_probe_error([r_php]) == "PHP Server Error (500)"

    # 8. Endpoint Not Found (404)
    r_404_a = requests.Response()
    r_404_a.status_code = 404
    r_404_b = requests.Response()
    r_404_b.status_code = 404
    assert _diagnose_probe_error([r_404_a, r_404_b]) == "Not Found (404)"

    # 9. Fallback / empty
    assert _diagnose_probe_error([]) == "Not NADA Server"


def test_display_server_stats_shows_unresolvable_diagnostics(monkeypatch, capsys) -> None:
    from dartfx.nada.harvester import display_server_stats
    from dartfx.nada.nada import ServerInstallation

    mock_insts = [
        ServerInstallation(name="BPS Indonesia", url="https://microdata.bps.go.id", country_code="ID"),
        ServerInstallation(name="Pacific Data Hub", url="https://microdata.pacificdata.org", country_code="VU"),
        ServerInstallation(name="APHRC", url="http://microdataportal.aphrc.org", country_code="KE"),
        ServerInstallation(name="ILO Retired", url="https://www.ilo.org/surveyLib", country_code="CH"),
        ServerInstallation(name="Armenia Stats", url="https://microdata.armstat.am", country_code="AM"),
    ]

    mock_stats = {
        "https://microdata.bps.go.id": {"is_nada": False, "error": "Perimeter Block (Geo/IP)", "latency_seconds": 1.2},
        "https://microdata.pacificdata.org": {
            "is_nada": False,
            "error": "Cloudflare Challenge",
            "latency_seconds": 0.4,
        },
        "http://microdataportal.aphrc.org": {
            "is_nada": False,
            "error": "Service Unavailable (503)",
            "latency_seconds": 0.8,
        },
        "https://www.ilo.org/surveyLib": {"is_nada": False, "error": "Catalog Retired (410)", "latency_seconds": 0.6},
        "https://microdata.armstat.am": {
            "is_nada": True,
            "studies": 42,
            "error": None,
            "latency_seconds": 1.5,
            "version": "5",
        },
    }

    monkeypatch.setattr("dartfx.nada.harvester.fetch_nada_installations", lambda **_kwargs: mock_insts)
    monkeypatch.setattr(
        "dartfx.nada.harvester.fetch_server_stats",
        lambda host, **_kwargs: mock_stats.get(host, {"is_nada": False, "error": "Unknown Error"}),
    )

    # 1. JSON output reports exact statuses, error reasons, and availability tiers
    records = display_server_stats(format="json")
    status_map = {r["name"]: r["status"] for r in records}
    avail_map = {r["name"]: r["availability"] for r in records}
    assert status_map["BPS Indonesia"] == "Perimeter Block (Geo/IP)"
    assert avail_map["BPS Indonesia"] == "restricted"
    assert status_map["Pacific Data Hub"] == "Cloudflare Challenge"
    assert avail_map["Pacific Data Hub"] == "restricted"
    assert status_map["APHRC"] == "Service Unavailable (503)"
    assert avail_map["APHRC"] == "offline"
    assert status_map["ILO Retired"] == "Catalog Retired (410)"
    assert avail_map["ILO Retired"] == "offline"
    assert status_map["Armenia Stats"] == "Online"
    assert avail_map["Armenia Stats"] == "accessible"

    # 2. Markdown output includes tri-state summary line and clear diagnostic rows
    capsys.readouterr()
    display_server_stats(format="markdown")
    captured_md = capsys.readouterr().out
    assert "Perimeter Block (Geo/IP)" in captured_md
    assert "Cloudflare Challenge" in captured_md
    assert "Service Unavailable (503)" in captured_md
    assert "Catalog Retired (410)" in captured_md
    assert "1 accessible • 2 restricted • 2 offline/inactive (5 servers)" in captured_md
    assert "42 total studies indexed" in captured_md


def test_classify_server_availability() -> None:
    from dartfx.nada.harvester import classify_server_availability

    # Accessible
    assert classify_server_availability(status="Online", is_nada=True) == "accessible"
    assert classify_server_availability(status="Online (AIA Fixed)", is_nada=True) == "accessible"
    assert classify_server_availability(status="Online (Cached)", is_nada=True) == "accessible"
    assert classify_server_availability(status="HTTP (SSL Failed)", is_nada=True) == "accessible"

    # Restricted
    assert classify_server_availability(status="Auth Required", is_nada=True, requires_token=True) == "restricted"
    assert classify_server_availability(status="Cloudflare Challenge", error="Cloudflare Challenge") == "restricted"
    assert (
        classify_server_availability(status="Perimeter Block (Geo/IP)", error="Perimeter Block (Geo/IP)")
        == "restricted"
    )
    assert classify_server_availability(status="Forbidden (403)", error="Forbidden (403)") == "restricted"

    # Offline / Inactive
    assert classify_server_availability(status="Timeout (10s)", error="Timeout (10s)") == "offline"
    assert classify_server_availability(status="DNS Resolution Failure", error="DNS Resolution Failure") == "offline"
    assert classify_server_availability(status="Domain For Sale", error="Domain For Sale") == "offline"
    assert classify_server_availability(status="Catalog Retired (410)", error="Catalog Retired (410)") == "offline"
    assert classify_server_availability(status="Not NADA Server") == "offline"


def test_get_ca_bundle_for_host(tmp_path: Path) -> None:
    from dartfx.nada.harvester import get_ca_bundle_for_host

    # When file does not exist
    assert get_ca_bundle_for_host("test.server.org", cache_dir=tmp_path) is None

    # When file exists and non-empty
    bundle_file = tmp_path / "test.server.org.pem"
    bundle_file.write_text("-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----", encoding="utf-8")
    assert get_ca_bundle_for_host("test.server.org", cache_dir=tmp_path) == str(bundle_file)


def test_resolve_aia_intermediate_ca(tmp_path: Path, monkeypatch) -> None:
    from dartfx.nada.harvester import resolve_aia_intermediate_ca

    # Mock socket and ssl to return a mock DER certificate with an AIA URL
    mock_der = b"some_der_bytes\x00http://pki.example.org/intermediate.crt\x00more_bytes"

    mock_sock = MagicMock()
    mock_ssock = MagicMock()
    mock_ssock.getpeercert.return_value = mock_der
    mock_sock.__enter__.return_value = mock_sock
    mock_ssock.__enter__.return_value = mock_ssock

    monkeypatch.setattr("socket.create_connection", lambda *_args, **_kwargs: mock_sock)
    mock_ctx = MagicMock()
    mock_ctx.wrap_socket.return_value = mock_ssock
    monkeypatch.setattr("ssl.create_default_context", lambda: mock_ctx)

    # Mock requests.get to return a PEM cert for the AIA URL
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"-----BEGIN CERTIFICATE-----\nMOCK_INTERMEDIATE\n-----END CERTIFICATE-----"
    monkeypatch.setattr("requests.get", lambda _url, **_kwargs: mock_resp)

    bundle_path = resolve_aia_intermediate_ca("test.nada.org", cache_dir=tmp_path)
    assert bundle_path is not None
    assert bundle_path.exists()
    content = bundle_path.read_text(encoding="utf-8")
    assert "MOCK_INTERMEDIATE" in content


def test_display_server_stats_shows_aia_fixed(monkeypatch) -> None:
    from dartfx.nada.harvester import display_server_stats
    from dartfx.nada.nada import ServerInstallation

    inst = ServerInstallation(
        name="Test AIA Server",
        hostname="anads.ansd.sn",
        url="https://anads.ansd.sn",
        country="Senegal",
        country_code="SN",
    )

    stat = {
        "is_nada": True,
        "studies": 120,
        "collections": 5,
        "version": "5",
        "latency_seconds": 1.2,
        "aia_repaired": True,
        "error": None,
    }

    monkeypatch.setattr("dartfx.nada.harvester.fetch_nada_installations", lambda **_kwargs: [inst])
    monkeypatch.setattr("dartfx.nada.harvester.fetch_server_stats", lambda *_args, **_kwargs: stat)

    records = display_server_stats(server="anads.ansd.sn", format="json")
    assert len(records) == 1
    assert records[0]["status"] == "Online (AIA Fixed)"
    assert records[0]["aia_repaired"] is True


def test_fetch_server_stats_resource_types(monkeypatch) -> None:
    def mock_get(url, **_kwargs):
        resp = MagicMock()
        if "api/catalog/search" in url:
            resp.status_code = 200
            resp.json = MagicMock(
                return_value={
                    "result": {
                        "total": 107,
                        "search_counts_by_type": {
                            "survey": 79,
                            "document": 28,
                        },
                        "rows": [],
                    }
                }
            )
        elif "api/catalog/collections" in url:
            resp.status_code = 200
            resp.json = MagicMock(return_value={"result": [{"id": 1}]})
        return resp

    monkeypatch.setattr("requests.get", mock_get)

    # Default: resource_type="survey" -> reports microdata count
    stats_default = fetch_server_stats("mock.server.org", refresh_cache=True)
    assert stats_default["is_nada"] is True
    assert stats_default["studies"] == 79
    assert stats_default["total_resources"] == 107
    assert stats_default["types"] == {"survey": 79, "document": 28}

    # Filter by specific type: document
    stats_doc = fetch_server_stats("mock.server.org", refresh_cache=True, resource_type="document")
    assert stats_doc["studies"] == 28

    # All resources: "all"
    stats_all = fetch_server_stats("mock.server.org", refresh_cache=True, resource_type="all")
    assert stats_all["studies"] == 107


def test_fetch_active_studies_resource_type_filter(monkeypatch) -> None:
    captured_urls = []

    def mock_get(url, **_kwargs):
        captured_urls.append(url)
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(
            return_value={
                "result": {
                    "total": 2,
                    "rows": [
                        {"idno": "SURV-1", "title": "Microdata 1", "type": "survey"},
                        {"idno": "DOC-1", "title": "Document 1", "type": "document"},
                    ],
                }
            }
        )
        return resp

    monkeypatch.setattr("requests.get", mock_get)

    # Default: resource_type="survey"
    studies = fetch_active_studies("mock.server.org", refresh_catalog=True)
    assert "SURV-1" in studies
    assert "DOC-1" not in studies
    assert any("&type=survey" in u for u in captured_urls)

    # All: resource_type="all"
    captured_urls.clear()
    studies_all = fetch_active_studies("mock.server.org", refresh_catalog=True, resource_type="all")
    assert "SURV-1" in studies_all
    assert "DOC-1" in studies_all
    assert not any("&type=all" in u for u in captured_urls)
