"""Sidecar Markdown generator for NADA server installations registry."""

from __future__ import annotations

import argparse
import importlib.resources
import os
from pathlib import Path
from typing import Any

import yaml

from dartfx.nada.nada import ISO2_TO_COUNTRY, ServerInstallation


def iso2_to_flag(code: str | None) -> str:
    """Convert an ISO 3166-1 alpha-2 country code to a Unicode flag emoji."""
    if not code or len(code) != 2:
        return "🌐"
    code = code.upper()
    try:
        return chr(0x1F1E6 + ord(code[0]) - ord("A")) + chr(0x1F1E6 + ord(code[1]) - ord("A"))
    except Exception:
        return "🌐"


def resolve_registry_paths(
    yaml_path: str | Path | None = None,
    md_path: str | Path | None = None,
) -> tuple[Path, Path]:
    """Resolve absolute paths for data/servers.yaml and its sidecar data/servers.md."""
    resolved_yaml: Path | None = None

    if yaml_path:
        resolved_yaml = Path(yaml_path).resolve()
    elif env_val := os.environ.get("NADA_SERVERS_FILE"):
        resolved_yaml = Path(env_val).resolve()
    else:
        # Search relative to repo root or CWD
        repo_candidate = Path(__file__).resolve().parents[3] / "data" / "servers.yaml"
        if repo_candidate.is_file():
            resolved_yaml = repo_candidate
        else:
            cwd_candidate = Path.cwd() / "data" / "servers.yaml"
            if cwd_candidate.is_file():
                resolved_yaml = cwd_candidate
            else:
                # Package resource fallback
                try:
                    pkg_files = importlib.resources.files("dartfx.nada")
                    pkg_yaml = pkg_files.joinpath("data", "servers.yaml")
                    if pkg_yaml.is_file():
                        resolved_yaml = Path(str(pkg_yaml))
                except Exception:
                    pass

    if not resolved_yaml or not resolved_yaml.is_file():
        # Default fallback to repo/cwd relative
        resolved_yaml = Path.cwd() / "data" / "servers.yaml"

    if md_path:
        resolved_md = Path(md_path).resolve()
    else:
        resolved_md = resolved_yaml.with_suffix(".md")

    return resolved_yaml, resolved_md


def render_servers_markdown(servers_data: list[dict[str, Any]]) -> str:
    """Render a structured list of server dictionary records into GitHub-flavored Markdown."""
    # Ensure items are parsed as ServerInstallation and sorted by hostname
    installations: list[ServerInstallation] = [
        ServerInstallation.model_validate(s) for s in servers_data if isinstance(s, dict)
    ]
    installations.sort(key=lambda s: (s.clean_hostname or s.hostname or "").lower())

    countries = {s.country_code for s in installations if s.country_code}

    lines: list[str] = [
        "# NADA Repositories Registry",
        "",
        "> **Automated Sidecar Registry**: This document is generated from [`data/servers.yaml`](servers.yaml).",
        "> To propose updates or register new servers, edit [`data/servers.yaml`](servers.yaml) or "
        "[open a GitHub Issue](https://github.com/DataArtifex/nada-toolkit/issues/new).",
        "",
        f"- **Total Registered Repositories**: {len(installations)}",
        f"- **Countries / Territories Represented**: {len(countries)}",
        "",
        "| # | Hostname | Repository / Catalog Name | Country | Custodian | Geographic Coverage | Domains |",
        "|---|---|---|---|---|---|---|",
    ]

    for idx, inst in enumerate(installations, start=1):
        host = inst.clean_hostname or inst.hostname or ""
        url = inst.catalog_url or inst.url or f"https://{host}"
        name = (inst.name or host).replace("|", "\\|")
        host_link = f"[{host}]({url})"

        # Country column
        code = inst.country_code
        flag = iso2_to_flag(code)
        country_name = inst.country or (ISO2_TO_COUNTRY.get(code.upper(), "") if code else "")
        if code:
            country_col = f"{flag} {country_name} (`{code}`)"
        else:
            country_col = "🌐 International / Regional"
        country_col = country_col.replace("|", "\\|")

        # Custodian
        if inst.custodian:
            c_name = (inst.custodian.name or "Custodian").replace("|", "\\|")
            if inst.custodian.url:
                cust_text = f"[{c_name}]({inst.custodian.url})"
            else:
                cust_text = c_name
            if inst.custodian.ror:
                cust_text += f" ([ROR]({inst.custodian.ror_url}))"
        else:
            cust_text = "—"

        # Geographic Coverage
        if inst.geocoverage:
            level = (inst.geocoverage.level or "").capitalize()
            geonames = inst.geocoverage.geonames or []
            geo_parts: list[str] = []
            if level:
                geo_parts.append(level)
            if geonames:
                geo_links = [f"[[GeoNames]({u})]" for u in geonames[:2]]
                geo_parts.append(" ".join(geo_links))
            geo_text = " ".join(geo_parts) if geo_parts else "—"
        else:
            geo_text = "—"
        geo_text = geo_text.replace("|", "\\|")

        # Domains
        if inst.domains:
            dom_text = ", ".join(f"`{d}`" for d in inst.domains)
        else:
            dom_text = "—"
        dom_text = dom_text.replace("|", "\\|")

        lines.append(f"| {idx} | {host_link} | {name} | {country_col} | {cust_text} | {geo_text} | {dom_text} |")

    lines.append("")
    return "\n".join(lines)


def generate_servers_markdown(
    yaml_path: str | Path | None = None,
    output_path: str | Path | None = None,
) -> tuple[Path, str]:
    """Generate sidecar data/servers.md from data/servers.yaml and write to disk."""
    src_yaml, dest_md = resolve_registry_paths(yaml_path, output_path)

    if not src_yaml.is_file():
        raise FileNotFoundError(f"Registry YAML file not found: {src_yaml}")

    with open(src_yaml, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, list):
        if isinstance(data, dict):
            data = data.get("servers") or data.get("installations") or []
        else:
            data = []

    markdown_content = render_servers_markdown(data)

    dest_md.parent.mkdir(parents=True, exist_ok=True)
    dest_md.write_text(markdown_content, encoding="utf-8")
    return dest_md, markdown_content


def check_servers_markdown(
    yaml_path: str | Path | None = None,
    md_path: str | Path | None = None,
) -> bool:
    """Check if the sidecar data/servers.md is in sync with data/servers.yaml."""
    src_yaml, dest_md = resolve_registry_paths(yaml_path, md_path)

    if not src_yaml.is_file() or not dest_md.is_file():
        return False

    with open(src_yaml, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, list):
        if isinstance(data, dict):
            data = data.get("servers") or data.get("installations") or []
        else:
            data = []

    expected = render_servers_markdown(data)
    actual = dest_md.read_text(encoding="utf-8")
    return expected == actual


def main() -> int:
    """CLI / pre-commit runner for registry markdown sidecar synchronization."""
    parser = argparse.ArgumentParser(description="Generate or verify sidecar data/servers.md from data/servers.yaml")
    parser.add_argument(
        "--yaml",
        "-y",
        dest="yaml_path",
        default=None,
        help="Path to servers.yaml (default: auto-detected data/servers.yaml)",
    )
    parser.add_argument(
        "--output",
        "-o",
        dest="output_path",
        default=None,
        help="Path to servers.md (default: sidecar to servers.yaml)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if servers.md is up to date without overwriting. Exits with 1 if out of sync.",
    )

    args = parser.parse_args()

    if args.check:
        in_sync = check_servers_markdown(args.yaml_path, args.output_path)
        if in_sync:
            print("data/servers.md is up to date.")
            return 0
        else:
            print("Drift detected: data/servers.md is out of sync with data/servers.yaml.")
            print("Run 'uv run python -m dartfx.nada.markdown' or 'dartfx-nada registry-md' to regenerate.")
            return 1

    dest_md, content = generate_servers_markdown(args.yaml_path, args.output_path)
    print(f"Successfully generated sidecar registry markdown at: {dest_md} ({len(content)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
