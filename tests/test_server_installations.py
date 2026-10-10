import json
import re
import sys

from typer.testing import CliRunner

from dartfx.nada.cli import _normalize_smart_dashes, app
from dartfx.nada.nada import (
    ServerInstallation,
    fetch_nada_installations,
    get_iso2_code,
    matches_country,
)


def test_fetch_nada_installations() -> None:
    installations = fetch_nada_installations()
    assert len(installations) > 0
    wb = next((inst for inst in installations if "worldbank" in (inst.hostname or "")), None)
    assert wb is not None
    assert wb.country_code == "US"


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
    us_servers = fetch_nada_installations(country="US")
    assert len(us_servers) > 0
    for s in us_servers:
        assert s.country_code == "US"

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

    result_csv = runner.invoke(app, ["installations", "--country", "US", "--limit", "2", "--format", "csv"])
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
