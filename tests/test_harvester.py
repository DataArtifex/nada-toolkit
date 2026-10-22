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
    assert abbreviate_error("Cloudflare challenge") == "Bot Protection (WAF)"
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
        lambda host, **_kwargs: mock_stats.get(host, {}),
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
