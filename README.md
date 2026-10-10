# dartfx-nada

[![Development Status](https://img.shields.io/badge/status-beta-orange.svg)](https://github.com/DataArtifex/nada-toolkit)
[![Documentation](https://img.shields.io/badge/docs-blue)](https://www.dataartifex.org/docs/dartfx-nada/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)
[![Contributor Covenant](https://img.shields.io/badge/Contributor%20Covenant-2.1-4baaaa.svg)](code_of_conduct.md)
[![License](https://img.shields.io/github/license/DataArtifex/nada-toolkit.svg)](https://github.com/DataArtifex/nada-toolkit/blob/main/LICENSE.txt)

**A modern Python toolkit and metadata harvester for IHSN NADA repositories**

> ⚠️ **Development Status**: This package provides high-level programmatic interaction and resilient metadata harvesting for NADA (National Data Archive) servers. Feedback and contributions are welcome!

---

## Overview

`dartfx-nada` is a type-safe Python toolkit and CLI utility for discovering, querying, and bulk-harvesting metadata from [IHSN NADA](https://nada.ihsn.org/) server instances.

NADA (National Data Archive) is an open-source data cataloging system widely used by national statistical offices (NSOs), central banks, international organizations (e.g., World Bank Microdata Library, ILO, UNHCR), and research institutions worldwide to publish microdata, survey documentation, and DDI metadata.

### Key Capabilities

- 🌍 **Worldwide Server Discovery**: Registry engine supporting known NADA deployments worldwide, with ISO 3166-1 alpha-2 country crosswalk and filtering.
- 🔍 **Catalog Search**: Comprehensive search client with support for keyword queries, collection filters, date ranges, and sorting.
- 📦 **Multi-Format Export & Retrieval**: Retrieve study metadata in DDI CodeBook 2.5 XML (`ddi`), NADA study metadata JSON (`json`), and documentation / external resources (`resources`).
- 🚜 **Resilient Incremental Harvester**:
  - Incremental diff synchronization (Additions, Updates, Deletions) backed by `.manifest.json`.
  - SHA-256 integrity verification and fast timestamp matching.
  - Automatic 24-hour catalog caching (`.catalog_cache.json`) and statistics caching (`.stats_cache.json`).
  - Automated API token discovery (`.api_token`, `.nada_tokens.json`, environment variables).
  - Intelligent Error Classification Engine categorizing network, authentication, restricted study, WAF, and SSL/TLS diagnostics.
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

List known worldwide NADA servers:

```bash
# List all known installations
dartfx-nada installations

# Filter by country code (e.g. US, ID, ZA, UG) or country name
dartfx-nada installations --country US

# Export list as JSON or CSV
dartfx-nada installations --format json --limit 10
dartfx-nada installations --format csv
```

### 2. Server Information & Collections

Inspect a server's version and catalog collections:

```bash
# Get server info and detected NADA version (v5 vs v4)
dartfx-nada info microdata.worldbank.org

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

Retrieve single study metadata or export documents:

```bash
# Get study JSON metadata
dartfx-nada study MWI_2019_IHS-V_v01_M -H microdata.worldbank.org

# Download DDI 2.5 CodeBook XML
dartfx-nada study MWI_2019_IHS-V_v01_M -H microdata.worldbank.org --export ddi

# Download external resources & documentation JSON
dartfx-nada study MWI_2019_IHS-V_v01_M -H microdata.worldbank.org --export resources
```

### 5. Resilient Metadata Harvester

Bulk-harvest and synchronize study metadata into local disk storage:

```bash
# Harvest studies from World Bank Microdata Library into ./repository
dartfx-nada harvest --server microdata.worldbank.org --limit 50 --formats ddi,json

# Harvest all supported formats (DDI XML, JSON metadata, resources)
dartfx-nada harvest --server nada.ubos.org --formats all

# Harvest studies across all servers in a country
dartfx-nada harvest --country ZA --formats ddi

# Dry-run mode (simulate without writing to disk)
dartfx-nada harvest --server microdata.worldbank.org --dry-run
```

### 6. Live Server Statistics & Latency

Benchmark and inspect live server status:

```bash
# Display live study and collection counts across servers
dartfx-nada stats

# Inspect servers in a specific country
dartfx-nada stats --country UG --refresh
```

### 7. Harvest Error Reporting & Diagnostics

Inspect error logs across harvested repository directories:

```bash
# Show summary of errors per error category
dartfx-nada errors ./repository

# Matrix breakdown by metadata format
dartfx-nada errors ./repository --by-format

# Breakdown by server
dartfx-nada errors ./repository --by-server

# Show detailed failed study IDs and reasons
dartfx-nada errors ./repository --details
```

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
│   │       ├── ddi.xml         # DDI 2.5 CodeBook XML
│   │       ├── nada.json       # Study metadata JSON
│   │       └── resources.json  # External resources JSON
```

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
