# dartfx-nada

[![Development Status](https://img.shields.io/badge/status-early%20release-orange.svg)](https://github.com/DataArtifex/nada-toolkit)
[![Documentation](https://img.shields.io/badge/docs-blue)](https://www.dataartifex.org/nada-toolkit/)
[![Ask DeepWiki](https://img.shields.io/badge/Ask%20DeepWiki-AI%20Docs-5C54E5)](https://deepwiki.com/DataArtifex/nada-toolkit)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Package Status](https://img.shields.io/badge/PyPI-not%20published-lightgrey)](https://github.com/DataArtifex/nada-toolkit)
[![CI](https://github.com/DataArtifex/nada-toolkit/actions/workflows/test.yml/badge.svg)](https://github.com/DataArtifex/nada-toolkit/actions/workflows/test.yml)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg?logo=ruff&logoColor=D7FF64)](https://github.com/astral-sh/ruff)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE.txt)
[![Contributor Covenant](https://img.shields.io/badge/Contributor%20Covenant-2.1-4baaaa.svg)](CODE_OF_CONDUCT.md)

**A modern Python toolkit and metadata harvester for IHSN NADA repositories**

> ⚠️ **Development Status**: This package provides high-level programmatic interaction and resilient metadata harvesting for NADA (National Data Archive) servers. Feedback and contributions are welcome!

---

## Overview

`dartfx-nada` is a type-safe Python toolkit and CLI utility for discovering, querying, and bulk-harvesting metadata from [IHSN NADA](https://nada.ihsn.org/) server instances.

NADA (National Data Archive) is an open-source data cataloging system widely used by national statistical offices (NSOs), central banks, international organizations (e.g., World Bank Microdata Library, ILO, UNHCR), and research institutions worldwide to publish microdata, survey documentation, and DDI metadata.

### Key Capabilities

- 🌍 **Worldwide Server Discovery**: Registry engine supporting known NADA deployments worldwide, with ISO 3166-1 alpha-2 country crosswalk and filtering.
- 🔍 **Catalog Search**: Comprehensive search client with support for keyword queries, collection filters, date ranges, and sorting.
- 📦 **Multi-Format Export & Retrieval**: Retrieve study metadata in DDI CodeBook 2.5 XML (`ddi`), NADA study metadata JSON (`json`), documentation / external resources (`resources`), MLCommons Croissant (`croissant`), and Google Dataset Search Schema.org (`schema`).
- 🚜 **Resilient Incremental Harvester**:
  - Concurrent multi-threaded execution (`--workers` / `-w`, default: 4) with thread-safe tracking and Rich progress visualization.
  - Incremental diff synchronization (Additions, Updates, Deletions) backed by `.manifest.json`.
  - SHA-256 integrity verification and fast timestamp matching.
  - Automatic 24-hour catalog caching (`.catalog_cache.json`) and statistics caching (`.stats_cache.json`).
  - Automated API token discovery (`.api_token`, `.nada_tokens.json`, environment variables).
  - Intelligent Error Classification Engine categorizing network, authentication, restricted study, WAF, and SSL/TLS diagnostics.
  - Comprehensive error persistence in `.manifest.json` with automatic retry of recoverable failures on consecutive syncs.
- 📊 **Server Diagnostics & Performance Latency**: Parallel query latency benchmarking with semantic color badges (`⚡`, `🟢`, `🟡`, `🔴`, `🔒`, `❌`).
- 🛡️ **Modern Architecture**: Python 3.12+, Pydantic v2 data models, Typer CLI, and Rich terminal visualizers.

---

## Installation

### Requirements

- Python 3.12 or higher
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

### Local Development Installation

```bash
git clone https://github.com/DataArtifex/nada-toolkit.git
cd nada-toolkit

# Using uv (fastest)
uv sync

# Or using pip
pip install -e ".[dev]"
```

---

## Command Line Interface (CLI)

The package installs the `dartfx-nada` CLI tool:

### 1. Server Discovery & Installations

List known worldwide NADA servers (`list` is the preferred command, with `installations` as an alias). The registry is maintained in [data/servers.yaml](data/servers.yaml) and can be updated directly by the community:

```bash
# List all known installations
dartfx-nada list

# Alternatively using the installations alias
dartfx-nada installations

# Filter by country code (e.g. US, ID, ZA, UG) or country name
dartfx-nada list --country US

# Export list as JSON or CSV
dartfx-nada list --format json --limit 10
dartfx-nada list --format csv
```

### 2. Server Information & Collections

Inspect a server's profile, total study counts, and supported export formats:

```bash
# Get server info, total study count, collections, and supported export formats
dartfx-nada info catalog.ihsn.org

# List collections in the repository
dartfx-nada collections --hostname microdata.worldbank.org
dartfx-nada collections --hostname microdata.worldbank.org --format csv
```

### 3. Catalog Search

Search studies across a NADA catalog:

```bash
# Search studies by keyword
dartfx-nada search "living standards" --hostname microdata.worldbank.org --limit 10

# Filter by country
dartfx-nada search "labor" --country "ID" --hostname sirusa.bps.go.id

# Export search results as JSON or CSV
dartfx-nada search "health" --format csv
```

### 4. Study Metadata & Export Formats

Retrieve single study metadata or export documents (including MLCommons Croissant):

```bash
# Get study JSON metadata
dartfx-nada study MWI_2019_IHS-V_v01_M -H microdata.worldbank.org

# Download DDI 2.5 CodeBook XML
dartfx-nada study MWI_2019_IHS-V_v01_M -H microdata.worldbank.org --export ddi

# Download MLCommons Croissant JSON-LD (AI/ML dataset metadata)
dartfx-nada study MWI_2019_IHS-V_v01_M -H microdata.worldbank.org --export croissant

# Download external resources & documentation JSON
dartfx-nada study MWI_2019_IHS-V_v01_M -H microdata.worldbank.org --export resources
```

### 5. Resilient Metadata Harvester

Bulk-harvest and synchronize study metadata into local disk storage:

```bash
# Harvest studies from World Bank Microdata Library using 8 concurrent worker threads
dartfx-nada harvest --server microdata.worldbank.org --limit 50 --formats ddi,json --workers 8

# Harvest all supported formats (DDI XML, JSON metadata, resources, Schema.org)
dartfx-nada harvest --server nada.ubos.org --formats all

# Harvest studies across all servers in a country
dartfx-nada harvest --country ZA --formats ddi

# Dry-run mode (simulate without writing to disk)
dartfx-nada harvest --server microdata.worldbank.org --dry-run
```

### 6. Live Server Statistics & Latency

Benchmark and inspect live server status:

# Display live study counts and detected PHP runtime (alphabetical by server name by default)
dartfx-nada stats

# Sort by study count (largest archives first)
dartfx-nada stats --sort studies

# Sort by response latency (fastest first) or country
dartfx-nada stats --sort latency
dartfx-nada stats --sort country

# Inspect servers in a specific country (forcing live bypass of 24h cache)
dartfx-nada stats --country UG --refresh

# Show extended server environment details (including web server banner)
dartfx-nada stats --show-env
```

#### Interpreting Server Environment & Status Columns

The `stats` table displays latency ratings, PHP runtime risk indicators, and diagnostic status badges:
- **PHP Version Column**:
  - **`Green`** (`>= 8.2`): Modern, active security support.
  - **`Yellow`** (`8.0 – 8.1`): End-of-Life (EOL), upgrade recommended.
  - **`Bold Red`** (`< 8.0`): Critical EOL (e.g. PHP 5.6, 7.4), elevated vulnerability risk.
  - **`Hidden`**: Headers stripped by reverse proxy, CDN, or `expose_php = Off`.
  - **`~5.x-7.x`** (Dim Red): Inferred legacy runtime when running NADA v4 with hidden banners.
- **`Online`**: Reachable NADA server returning active study counts.
- **`Cached (24h)`**: Cached metrics loaded from local `.stats_cache.json` (use `--refresh` to force live checks).
- **`Online (HTTPS Avail)`**: Server currently reached over HTTP, but HTTPS is fully available and recommended for upgrade.
- **`HTTP (SSL Failed)`**: Server failed SSL/TLS verification on HTTPS, but study counts and catalog access were successfully recovered via HTTP port 80.
- **`Not NADA Server`**: The domain is active but does not run a valid NADA API or catalog endpoint (e.g. portal redirects, general government websites).
- **`Domain For Sale`**: Domain has lapsed and redirects to a registrar parking/auction page.
- **`SSL Incomplete Chain`**: The server is missing intermediate CA certificates (`cert.pem` served without `fullchain.pem`). While web browsers automatically repair this in the background using AIA fetching or intermediate caching, standard OpenSSL/Python clients strictly require the complete chain.
- **`SSL Hostname Mismatch` / `SSL Expired`**: Certificate is misconfigured or expired.
- **`Timeout (10s)` / `DNS Resolution Failure`**: Host unreachable within 10 seconds or domain no longer resolves.


### 7. Harvest Error Reporting & Diagnostics

Inspect error logs across harvested repository directories:

```bash
# Show summary of errors across all servers (defaults to DARTFX_NADA_REPOSITORY or ./repository)
dartfx-nada errors

# Filter errors by server
dartfx-nada errors --server catalog.ihsn.org

# Matrix breakdown by metadata format
dartfx-nada errors --by-format

# Breakdown by server
dartfx-nada errors --by-server

# Show detailed failed study IDs and failure reasons
dartfx-nada errors --server catalog.ihsn.org --details
````

---

## Python API Usage

### 1. Connecting to a NADA Server

```python
from dartfx.nada import NadaServer, SearchParameters

# Connect to World Bank Microdata Library
server = NadaServer("microdata.worldbank.org")

# Autodetect version
version = server.detect_server_version()
print(f"Server version: v{version}")

# Search catalog
params = SearchParameters(sk="agriculture", ps=5)
results = server.search(params)
for study in results["result"]["rows"]:
    print(f"[{study['idno']}] {study['title']} ({study.get('nation')})")
```

### 2. Exporting Study Documents

```python
# Download DDI XML
ddi_xml = server.get_study_ddi("MWI_2019_IHS-V_v01_M")

# Download study JSON metadata
meta_json = server.get_study_json("MWI_2019_IHS-V_v01_M")

# Download external documentation / questionnaire resources
resources = server.get_study_resources("MWI_2019_IHS-V_v01_M")

# Download MLCommons Croissant JSON-LD
croissant = server.get_study_croissant("MWI_2019_IHS-V_v01_M")

# Download Google Dataset Search Schema.org JSON-LD
schema_jsonld = server.get_study_schema("MWI_2019_IHS-V_v01_M")
```

### 3. Programmatic Metadata Harvester

```python
from pathlib import Path
from dartfx.nada import ServerHarvester

repo_dir = Path("./my_repository")
harvester = ServerHarvester("microdata.worldbank.org", repo_dir)

# Synchronize up to 25 studies incrementally
stats = harvester.sync(
    limit=25,
    metadata_formats=["ddi", "json"],
    verbose=True,
)

print(f"Added: {len(stats['added'])}, Updated: {len(stats['updated'])}, Unchanged: {len(stats['unchanged'])}")
```

---

## Repository Directory Layout

Harvested repositories are organized in a clean, reproducible filesystem hierarchy:

```
repository/
├── microdata.worldbank.org/
│   ├── .manifest.json          # Tracks SHA-256 hashes, timestamps, error records
│   ├── .stats_cache.json       # 24-hour server statistics cache
│   ├── .catalog_cache.json     # 24-hour study discovery cache
│   ├── MWI_2019_IHS_V_v01_M/
│   │   └── metadata/
│   │       ├── ddi.xml          # DDI 2.5 CodeBook XML
│   │       ├── nada.json        # Study metadata JSON
│   │       ├── resources.json   # External resources JSON
│   │       ├── croissant.jsonld # MLCommons Croissant JSON-LD
│   │       └── schema.jsonld    # Google Dataset Search Schema.org JSON-LD
```

---

## Contributing to the NADA Repositories Registry

The worldwide registry of NADA installations is stored in [`data/servers.yaml`](data/servers.yaml), sorted alphabetically by `hostname`. Because `dartfx-nada` automatically fetches this file directly from the GitHub repository over HTTPS when running `dartfx-nada list`, additions and updates become available to all users immediately upon being merged into `main`!

There are two easy ways to add or update a NADA server:

### Option 1: Edit `data/servers.yaml` via GitHub Pull Request (Recommended)

Non-developers can edit the file directly in their browser:
1. Navigate to [`data/servers.yaml`](https://github.com/DataArtifex/nada-toolkit/blob/main/data/servers.yaml) on GitHub.
2. Click the pencil icon (✏️ **Edit this file**) in the top-right corner (GitHub will automatically create a fork for you).
3. Insert or update the server entry keeping entries **sorted alphabetically by `hostname`**.
4. Click **Propose changes** and submit a **Pull Request**.

### Option 2: Open a GitHub Issue

If you prefer not to edit YAML directly, simply [open a GitHub Issue](https://github.com/DataArtifex/nada-toolkit/issues/new) titled `Add NADA Server: <Organization Name>` and provide:
- **Server URL**: (e.g., `https://microdata.example.org`)
- **Organization Name**: (e.g., `National Statistical Office`)
- **Country**: (e.g., `Senegal` / `SN`)
- **Custodian Homepage & ROR ID** (optional, e.g. `0539hqx70`)
- **Topical / Domain Coverage** (optional, e.g. `census`, `agriculture`, `official-statistics`)

### Registry YAML Schema

Each entry in [`data/servers.yaml`](data/servers.yaml) adheres to the following specification:

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | `string` | **Yes** | Human-readable name of the catalog or repository. |
| `url` | `string` | **Yes** | Full web catalog endpoint URL (hostname is derived automatically). |
| `country_code` | `string` | Optional | Two-letter ISO 3166-1 alpha-2 code (e.g. `SN`, `UG`, `US`), or null for global/international agencies. |
| `custodian` | `object` | Optional | Custodian organization details (`name`, `url`, `ror` identifier). |
| `geocoverage` | `object` | Optional | Scope (`level`: `global` \| `regional` \| `national` \| `subnational` \| `city` \| `other`), textual `description`, and `geonames` list of URIs. |
| `domains` | `list` | Optional | List of thematic tags (e.g. `official-statistics`, `census`, `socio-economic`, `agriculture`). |

#### Example Entry

```yaml
- name: Millennium Challenge Corporation
  url: https://mcc.icpsr.umich.edu/evaluations/index.php/catalog
  country_code: US
  custodian:
    name: Millennium Challenge Corporation (MCC)
    url: https://www.mcc.gov
    ror: 0539hqx70
  geocoverage:
    level: national
    description: United States
    geonames:
      - https://www.geonames.org/6252001/
  domains:
    - official-statistics
    - socio-economic
    - census
```

### Sidecar Markdown Generation & Automated CI/Commit Synchronization

A human-readable Markdown directory is automatically maintained alongside the YAML at [`data/servers.md`](data/servers.md).

- **Regenerate manually or via CLI**:
  ```bash
  # Regenerate data/servers.md from data/servers.yaml
  dartfx-nada registry-md

  # Or verify that data/servers.md is in sync (exits with code 1 if drift detected)
  dartfx-nada registry-md --check
  ```
- **Automated on Git Commit (`pre-commit`)**: The project's `.pre-commit-config.yaml` includes a hook that triggers on changes to `data/servers.yaml` to ensure `data/servers.md` is updated before committing.
- **Automated in CI / Push / PR**: GitHub Actions runs `python -m dartfx.nada.markdown --check` during the lint job, ensuring pull requests remain in sync with the YAML source of truth.


---

## Development & Quality Assurance

This repository strictly adheres to Python 3.12+ standards, Ruff linting/formatting, Pyrefly type checking, and Pytest suites.

```bash
# Run test suite
uv run pytest

# Check formatting & linting
uv run ruff check .
uv run ruff format . --check

# Type check
uv run pyrefly check src tests
```

---

## License

This project is licensed under the MIT License. See [LICENSE.txt](LICENSE.txt) for details.
