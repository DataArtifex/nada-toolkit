# Technical Implementation

This document describes the technical architecture and implementation details of `dartfx-nada`.

## Architecture Overview

The toolkit is designed to be a high-level, type-safe wrapper around the IHSN NADA (National Data Archive) Catalog and API services. It prioritizes discovery, catalog browsing, and resilient metadata harvesting.

### Core Components

1.  **`NadaServer`**: The primary interface for API interactions.
    - Inherits from `pydantic.BaseModel` for configuration and state validation.
    - Manages `requests-cache` sessions for network performance and cache transparency.
    - Automatic version detection (`detect_server_version`) identifying NADA v5 (`/api/`) vs v4 (`/index.php/api/`) endpoint prefixes.
    - Provides high-level methods for Catalog Search, Collection browsing, Country/Topic codes, and Study exports (`ddi`, `json`, `resources`).
2.  **`ServerInstallation`**: A Pydantic model representing a NADA repository installation with rich metadata (name, hostname, coordinates, launch year, ISO 3166-1 alpha-2 `country_code`, description, etc.) and helper properties (`clean_hostname`, `url`).
3.  **`SearchParameters`**: A comprehensive Pydantic model validating NADA Catalog Search API query parameters (pagination, keyword searches, collection/country/topic filters, date ranges, and sorting).
4.  **`fetch_nada_installations` & Registry Engine**: The centralized registry subsystem for discovering worldwide NADA instances.
    - Multi-source registry loading (remote or local CSV/JSON endpoints with configurable timeout).
    - Built-in ISO 3166-1 Alpha-2 country crosswalk (`COUNTRY_TO_ISO2`, `get_iso2_code`, `matches_country`).
    - Filtering by hostname, ISO country code, or country substring.
    - Automatic fallback synthesis for unlisted, private, or development NADA deployments.
5.  **`ServerHarvester` / Metadata Harvester**: A resilient bulk metadata synchronization subsystem.
    - Consumes the unified `fetch_nada_installations` registry engine.
    - Multi-format harvesting: DDI 2.5 CodeBook XML (`ddi`), Study Metadata JSON (`json`), and External Resources & Documentation (`resources`).
    - **Study-Level Packaging Granularity**: Exports are fetched per-study from NADA endpoints (`/catalog/ddi/{idno}`, `/catalog/json/{idno}`, `/catalog/resources/{idno}`).
    - Incremental diff synchronization (Additions, Updates, Unchanged, Deletions) backed by `.manifest.json`.
    - Fast timestamp checking and SHA-256 integrity verification.
    - 24-hour catalog (`.catalog_cache.json`) and server statistics (`.stats_cache.json`) caching.
    - Automated API token discovery and resolution (`.api_token`, `.nada_tokens.json`, environment variables).
    - **Intelligent Error Classification Engine** (`classify_harvest_error`): Categorizes failure reasons into distinct taxonomies (Study Not Found HTTP 404, Authentication Required HTTP 401, Restricted Study HTTP 403, WAF / Bot Protection HTTP 403, Server Errors HTTP 5xx, Network Timeouts, Parse Errors, and SSL/TLS Diagnostics).
6.  **Server Statistics, Performance Latency & Diagnostics Subsystem**:
    - **Concurrent Metric Queries**: Queries study counts and collections in parallel via `ThreadPoolExecutor` for low response latency.
    - **Performance Tier Rating Engine** (`format_response_latency`): Classifies response times into semantic color-coded tiers (`⚡ <3s` Fast, `🟢 3–8s` Normal, `🟡 8–15s` Moderate, `🔴 >15s` Degraded, `🔒` Protected, `❌` Failed).
    - **Deep SSL/TLS Diagnostics** (`classify_harvest_error`): Inspects peer certificates and verification error chains to isolate incomplete intermediate CA chains, certificate expirations, hostname mismatches, and self-signed certificates.
    - **24-Hour Persistence**: Stores metrics in `.stats_cache.json` under each server directory for sub-second CLI re-rendering.

## Design Decisions

### Pydantic for Modeling
We use Pydantic models for all data structures (input parameters and server configurations) instead of standard Python dataclasses. This provides:
- Automatic type validation and coercion.
- Clean serialization and deserialization (`model_dump`, `model_dump_json`).
- Improved IDE autocompletion and static analysis support.

### Request & Catalog Caching
Transparent HTTP caching via `requests-cache` is built-in for interactive API sessions. Additionally, the harvesting subsystem utilizes disk-backed, TTL-managed catalog and statistics caches with configurable refresh flags.

### Error Handling
A custom `NadaApiError` exception provides detailed information about API failures, including status codes, request URLs, and raw response content. The harvester engine categorizes network vs schema/permission failures into non-recoverable error registries to eliminate redundant HTTP requests on subsequent runs.

### Strict Typing & Testing
The project follows strict type hinting and is continuously validated using `ruff`, `pyrefly`, and `pytest`.

## External Dependencies

- `requests`: Base HTTP library.
- `requests-cache`: Transparent caching for HTTP requests.
- `pydantic`: Data validation and settings management.
- `typer` & `rich`: Command-line interface and terminal reports.
- `python-dotenv`: Environment configuration loading.
- `polars`: Preferred for tabular data processing.
