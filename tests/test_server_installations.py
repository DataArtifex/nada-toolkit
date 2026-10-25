import json
import re
import sys
from pathlib import Path

from typer.testing import CliRunner

from dartfx.nada.cli import _normalize_smart_dashes, app
from dartfx.nada.nada import (
    Custodian,
    ServerInstallation,
    fetch_nada_installations,
    get_iso2_code,
    load_bundled_servers_yaml,
    matches_country,
)


def test_fetch_nada_installations() -> None:
    installations = fetch_nada_installations()
    assert len(installations) > 0
    wb = next((inst for inst in installations if "worldbank" in (inst.hostname or "")), None)
    assert wb is not None
    assert wb.clean_hostname == "microdata.worldbank.org"


def test_server_installation_country_code_derivation() -> None:
    inst = ServerInstallation(
        name="Stats SA",
        hostname="https://interactive.statssa.gov.za/",
        country="South Africa",
    )
    assert inst.country_code == "ZA"
    assert inst.clean_hostname == "interactive.statssa.gov.za"
    assert inst.url == "https://interactive.statssa.gov.za"


def test_country_crosswalk_and_matching() -> None:
    assert get_iso2_code("United States") == "US"
    assert get_iso2_code("Indonesia") == "ID"
    assert get_iso2_code("South Africa") == "ZA"
    assert get_iso2_code("Uganda") == "UG"
    assert get_iso2_code("Senegal") == "SN"
    assert get_iso2_code("Rwanda") == "RW"
    assert get_iso2_code("US") == "US"

    assert matches_country("US", "United States") is True
    assert matches_country("US", "Indonesia") is False
    assert matches_country("ZA", "South Africa") is True
    assert matches_country("uganda", "Uganda") is True
    assert matches_country("indo", "Indonesia") is True


def test_fetch_nada_installations_filtering() -> None:
    # Country filter
    ug_servers = fetch_nada_installations(country="UG")
    assert len(ug_servers) > 0
    for s in ug_servers:
        assert s.country_code == "UG"

    # Specific known target server filter
    wb_list = fetch_nada_installations(target_server="microdata.worldbank.org")
    assert len(wb_list) == 1
    assert wb_list[0].clean_hostname == "microdata.worldbank.org"

    # Unlisted fallback
    fallback_list = fetch_nada_installations(target_server="private.nada.internal", fallback_unlisted=True)
    assert len(fallback_list) == 1
    assert fallback_list[0].clean_hostname == "private.nada.internal"
    assert fallback_list[0].country_code == "-"


def test_cli_installations_command(monkeypatch) -> None:
    mock_insts = [
        ServerInstallation(name="Test Server 1", hostname="nada1.test", country="United States"),
        ServerInstallation(name="Test Server 2", hostname="nada2.test", country="United States"),
    ]
    monkeypatch.setattr("dartfx.nada.cli.fetch_nada_installations", lambda **_kwargs: mock_insts)

    runner = CliRunner()
    result = runner.invoke(app, ["installations", "--country", "US", "--limit", "2", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert len(data) == 2
    assert all(d.get("country_code") == "US" for d in data)

    # Test list as preferred alias
    result_list = runner.invoke(app, ["list", "--country", "US", "--limit", "2", "--format", "json"])
    assert result_list.exit_code == 0
    data_list = json.loads(result_list.stdout)
    assert len(data_list) == 2
    assert all(d.get("country_code") == "US" for d in data_list)

    result_csv = runner.invoke(app, ["list", "--country", "US", "--limit", "2", "--format", "csv"])
    assert result_csv.exit_code == 0
    assert "country_code" in result_csv.stdout
    assert "US" in result_csv.stdout


def test_cli_smart_dash_normalization(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["dartfx-nada", "stats", "-–country", "US", "—limit", "5"])
    _normalize_smart_dashes()
    assert sys.argv == ["dartfx-nada", "stats", "--country", "US", "--limit", "5"]


def test_cli_stats_command_flags() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["stats", "--help"])
    assert result.exit_code == 0
    clean_output = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", result.stdout)
    assert "--refresh" in clean_output
    assert "-r" in clean_output
    assert "--cache-ttl" in clean_output
    assert "--timeout" in clean_output
    assert "-t" in clean_output
    assert "--show-env" in clean_output
    assert "-e" in clean_output
    assert "--sort" in clean_output
    assert "-S" in clean_output
    assert "--desc" in clean_output
    assert "--asc" in clean_output
    assert "--format" in clean_output
    assert "-f" in clean_output
    assert "--json" in clean_output
    assert "--markdown" in clean_output
    assert "--md" in clean_output
    assert "--output" in clean_output
    assert "-o" in clean_output


def test_cli_stats_json_and_markdown(monkeypatch) -> None:
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

    runner = CliRunner()

    # Test --json flag
    res_json = runner.invoke(app, ["stats", "--json", "--country", "AM"])
    assert res_json.exit_code == 0
    parsed = json.loads(res_json.stdout)
    assert len(parsed) == 1
    assert parsed[0]["name"] == "Armenia Stats"
    assert parsed[0]["studies"] == 26

    # Test --markdown flag
    res_md = runner.invoke(app, ["stats", "--markdown", "--country", "AM"])
    assert res_md.exit_code == 0
    assert "| Repository / Server | Country | Ver | PHP | Studies | Latency | Status |" in res_md.stdout
    assert "[Armenia Stats](https://microdata.armstat.am)" in res_md.stdout

    # Test --md alias
    res_md_alias = runner.invoke(app, ["stats", "--md", "--country", "AM"])
    assert res_md_alias.exit_code == 0
    assert "[Armenia Stats](https://microdata.armstat.am)" in res_md_alias.stdout

    # Test --format json
    res_fmt_json = runner.invoke(app, ["stats", "-f", "json", "--country", "AM"])
    assert res_fmt_json.exit_code == 0
    assert json.loads(res_fmt_json.stdout)[0]["studies"] == 26

    # Test -o with specific file
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_f = Path(tmp_dir) / "out.json"
        res_file = runner.invoke(app, ["stats", "--json", "-o", str(out_f), "--country", "AM"])
        assert res_file.exit_code == 0
        assert out_f.exists()
        assert "Statistics saved to" in res_file.stdout
        loaded = json.loads(out_f.read_text("utf-8"))
        assert loaded[0]["name"] == "Armenia Stats"

        # Test --repo-dir option
        res_repo = runner.invoke(app, ["stats", "--repo-dir", tmp_dir, "--country", "AM"])
        assert res_repo.exit_code == 0

        # Test -R shorthand option
        res_repo_short = runner.invoke(app, ["stats", "-R", tmp_dir, "--country", "AM"])
        assert res_repo_short.exit_code == 0


def test_cli_version_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    from dartfx.nada.__about__ import __version__

    assert result.exit_code == 0
    assert __version__ in result.stdout

    result_short = runner.invoke(app, ["-V"])
    assert result_short.exit_code == 0
    assert __version__ in result_short.stdout


def test_load_bundled_servers_yaml() -> None:
    servers = load_bundled_servers_yaml()
    assert len(servers) >= 60
    wb = next((s for s in servers if "worldbank" in (s.get("url", "") or s.get("hostname", ""))), None)
    assert wb is not None
    assert "World Bank" in wb["name"]
    assert wb.get("geocoverage", {}).get("level") == "global"

    sn = next((s for s in servers if "ansd.sn" in (s.get("url", "") or s.get("hostname", ""))), None)
    assert sn is not None
    assert sn["country_code"] == "SN"
    assert sn.get("geocoverage", {}).get("level") == "national"


def test_fetch_nada_installations_local_yaml(tmp_path: Path) -> None:
    custom_yaml = tmp_path / "custom_servers.yaml"
    custom_yaml.write_text(
        """
- name: Custom Test Archive
  hostname: nada.custom.org
  url: https://nada.custom.org
  country: Testland
  country_code: TL
  version: 5
  status: up
""",
        encoding="utf-8",
    )
    insts = fetch_nada_installations(urls=[str(custom_yaml)])
    assert len(insts) == 1
    assert insts[0].name == "Custom Test Archive"
    assert insts[0].clean_hostname == "nada.custom.org"
    assert insts[0].country_code == "TL"
    assert insts[0].url == "https://nada.custom.org"


def test_server_installation_url_and_scheme_normalization() -> None:
    # URL provided instead of hostname
    inst_http = ServerInstallation(
        name="HTTP Test",
        url="http://custom.nada.org/subpath",
        country="Senegal",
    )
    assert inst_http.scheme == "http"
    assert inst_http.clean_hostname == "custom.nada.org"
    assert inst_http.url == "http://custom.nada.org/subpath"
    assert inst_http.country_code == "SN"


def test_server_installation_country_derivation_from_code() -> None:
    # Only country_code provided
    inst = ServerInstallation(
        name="ANSD Senegal",
        hostname="anads.ansd.sn",
        country_code="SN",
    )
    assert inst.country == "Senegal"
    assert inst.country_code == "SN"


def test_server_installation_custodian_and_geocoverage() -> None:
    inst = ServerInstallation(
        name="World Bank Microdata Library",
        hostname="microdata.worldbank.org",
        country_code="US",
        custodian={
            "name": "World Bank Group",
            "home_page": "https://www.worldbank.org",
            "ror_uri": "https://ror.org/02md09461",
        },
        geocoverage={
            "level": "global",
            "description": "Worldwide",
            "geonames": ["https://www.geonames.org/6255147/"],
        },
        domains=["socio-economic", "living-standards"],
    )
    assert inst.custodian is not None
    assert inst.custodian.name == "World Bank Group"
    assert inst.custodian.url == "https://www.worldbank.org"
    assert inst.custodian.ror == "02md09461"
    assert inst.custodian.ror_url == "https://ror.org/02md09461"
    assert inst.custodian.ror_uri == "https://ror.org/02md09461"
    assert inst.geocoverage is not None
    assert inst.geocoverage.level == "global"
    assert inst.geocoverage.geonames == ["https://www.geonames.org/6255147/"]
    assert inst.geocoverage.geonames_uri == "https://www.geonames.org/6255147/"
    assert inst.domains == ["socio-economic", "living-standards"]

    # Test direct Custodian identifier and URL composition
    cust = Custodian(name="MCC", url="https://www.mcc.gov", ror="0539hqx70")
    assert cust.ror == "0539hqx70"
    assert cust.ror_url == "https://ror.org/0539hqx70"
    assert cust.ror_uri == "https://ror.org/0539hqx70"

    # Test full URL normalization
    cust_normalized = Custodian(name="MCC", ror="https://ror.org/0539hqx70/")
    assert cust_normalized.ror == "0539hqx70"
    assert cust_normalized.ror_url == "https://ror.org/0539hqx70"

    # Test single geonames_uri string backwards-compatibility input
    inst_compat = ServerInstallation(
        name="Test",
        hostname="test.org",
        geocoverage={"geonames_uri": "https://www.geonames.org/123/"},
    )
    assert inst_compat.geocoverage is not None
    assert inst_compat.geocoverage.geonames == ["https://www.geonames.org/123/"]
    assert inst_compat.geocoverage.geonames_uri == "https://www.geonames.org/123/"


def test_agris_monitor_server_additions() -> None:
    """Verify that servers identified on the AGRIS monitor are present in bundled servers.yaml and sorted."""
    servers = load_bundled_servers_yaml()
    country_codes = {s.get("country_code") for s in servers}
    # Check that new countries from the AGRIS monitor are present
    for code in ["CV", "GM", "GN", "MG", "ZM", "BJ", "BF", "CI", "ML", "NP", "NG", "SL", "TZ"]:
        assert code in country_codes, f"Expected {code} in servers.yaml"

    # Verify Cabo Verde, Gambia, and Zambia entries
    cv_server = next(s for s in servers if s.get("country_code") == "CV")
    assert "ine.cv" in (cv_server.get("url", "") or cv_server.get("hostname", ""))
    assert cv_server["custodian"]["name"] == "Instituto Nacional de Estatística (INE)"
    assert isinstance(cv_server["geocoverage"]["geonames"], list)

    gm_server = next(s for s in servers if s.get("country_code") == "GM")
    assert "gbosdata.org" in (gm_server.get("url", "") or gm_server.get("hostname", ""))

    zm_server = next(s for s in servers if s.get("country_code") == "ZM")
    assert "zamstats.gov.zm" in (zm_server.get("url", "") or zm_server.get("hostname", ""))

    # Verify that servers list is sorted by hostname
    insts = [ServerInstallation.model_validate(s) for s in servers]
    hostnames = [inst.clean_hostname.lower() for inst in insts]
    assert hostnames == sorted(hostnames), "Servers should be sorted alphabetically by hostname"
