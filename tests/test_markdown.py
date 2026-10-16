from pathlib import Path

import yaml
from typer.testing import CliRunner

from dartfx.nada.cli import app
from dartfx.nada.markdown import (
    check_servers_markdown,
    generate_servers_markdown,
    iso2_to_flag,
    render_servers_markdown,
)


def test_iso2_to_flag() -> None:
    assert iso2_to_flag("SN") == "🇸🇳"
    assert iso2_to_flag("US") == "🇺🇸"
    assert iso2_to_flag("ID") == "🇮🇩"
    assert iso2_to_flag(None) == "🌐"
    assert iso2_to_flag("") == "🌐"
    assert iso2_to_flag("GLOBAL") == "🌐"


def test_render_servers_markdown() -> None:
    sample_data = [
        {
            "name": "B Server",
            "hostname": "b.example.org",
            "url": "https://b.example.org/catalog",
            "country_code": "SN",
            "custodian": {
                "name": "Agency B",
                "url": "https://agency-b.sn",
                "ror": "https://ror.org/12345",
            },
            "geocoverage": {
                "level": "national",
                "description": "Senegal",
                "geonames": ["https://www.geonames.org/2245662/"],
            },
            "domains": ["official-statistics", "census"],
        },
        {
            "name": "A Server",
            "hostname": "a.example.org",
            "url": "https://a.example.org",
            "country_code": None,
        },
    ]

    rendered = render_servers_markdown(sample_data)
    assert "# NADA Repositories Registry" in rendered
    assert "- **Total Registered Repositories**: 2" in rendered
    assert "🇸🇳 Senegal (`SN`)" in rendered
    assert "🌐 International / Regional" in rendered
    assert "[a.example.org](https://a.example.org)" in rendered
    assert "[b.example.org](https://b.example.org/catalog)" in rendered
    assert "[Agency B](https://agency-b.sn) ([ROR](https://ror.org/12345))" in rendered
    assert "National [[GeoNames](https://www.geonames.org/2245662/)]" in rendered
    assert "`official-statistics`, `census`" in rendered

    # Verify alphabetical ordering by hostname: a.example.org before b.example.org
    pos_a = rendered.find("a.example.org")
    pos_b = rendered.find("b.example.org")
    assert pos_a < pos_b


def test_generate_and_check_servers_markdown(tmp_path: Path) -> None:
    test_yaml = tmp_path / "servers.yaml"
    test_md = tmp_path / "servers.md"

    data = [
        {
            "name": "Alpha Catalog",
            "hostname": "alpha.org",
            "url": "https://alpha.org",
            "country_code": "US",
        }
    ]
    test_yaml.write_text(yaml.dump(data), encoding="utf-8")

    # Before generation, check should return False
    assert check_servers_markdown(test_yaml, test_md) is False

    # Generate
    out_path, content = generate_servers_markdown(test_yaml, test_md)
    assert out_path == test_md
    assert test_md.is_file()
    assert "Alpha Catalog" in content

    # After generation, check should return True
    assert check_servers_markdown(test_yaml, test_md) is True

    # Modify YAML to introduce drift
    data.append(
        {
            "name": "Beta Catalog",
            "hostname": "beta.org",
            "url": "https://beta.org",
            "country_code": "CA",
        }
    )
    test_yaml.write_text(yaml.dump(data), encoding="utf-8")

    # Drift should be detected
    assert check_servers_markdown(test_yaml, test_md) is False

    # Regenerate
    generate_servers_markdown(test_yaml, test_md)
    assert check_servers_markdown(test_yaml, test_md) is True


def test_cli_registry_md(tmp_path: Path) -> None:
    runner = CliRunner()
    test_yaml = tmp_path / "servers.yaml"
    test_md = tmp_path / "servers.md"

    data = [
        {
            "name": "CLI Catalog",
            "hostname": "cli.org",
            "url": "https://cli.org",
            "country_code": "SN",
        }
    ]
    test_yaml.write_text(yaml.dump(data), encoding="utf-8")

    # Generate via CLI
    res = runner.invoke(app, ["registry-md", "-y", str(test_yaml), "-o", str(test_md)])
    assert res.exit_code == 0
    assert "Successfully generated" in res.stdout
    assert test_md.is_file()

    # Check via CLI
    res_check = runner.invoke(app, ["registry-md", "-y", str(test_yaml), "-o", str(test_md), "--check"])
    assert res_check.exit_code == 0
    assert "is up to date" in res_check.stdout

    # Introduce drift
    data.append({"name": "New", "hostname": "new.org", "url": "https://new.org"})
    test_yaml.write_text(yaml.dump(data), encoding="utf-8")

    res_drift = runner.invoke(app, ["registry-md", "-y", str(test_yaml), "-o", str(test_md), "--check"])
    assert res_drift.exit_code == 1
    assert "Drift detected" in res_drift.stdout
