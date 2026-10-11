# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-10-10

### Added

- **First-Class Metadata Harvester & Sync Subsystem** (`dartfx-nada harvest`):
  - Incremental, hash-verified synchronization of study metadata across NADA servers.
  - Multi-threaded concurrent worker execution (`--workers` / `-w`, default: 4) using `ThreadPoolExecutor` with thread-safe manifest tracking, error recording, file writes, and Rich progress visualization.
  - Stabilized download progress bar layout by anchoring server name on the left, fixing bar/count/timer widths, and placing the active filename on the right-hand side to eliminate visual shifting.
  - Fixed harvest timer display by resetting progress state upon transition from catalog discovery to study synchronization and using `TimeElapsedColumn` for active stopwatch timing.
  - Multi-format harvesting supporting DDI 2.5 CodeBook XML (`ddi`), Study Metadata JSON (`json`), External Documentation/Resources (`resources`), MLCommons Croissant (`croissant`), and Google Dataset Search Schema.org (`schema` / `.schema.jsonld`), as well as `--formats all`.
  - Cumulative catalog cache merging and partial-cache expansion handling, ensuring higher `--limit` requests query live servers while retaining previously cached studies.
  - SHA-256 integrity verification and fast timestamp checking backed by `.manifest.json`.
  - Deletion pruning protection during filtered sync runs (only prune deletions during full, unconstrained server syncs).
  - 24-hour local catalog caching (`.catalog_cache.json`) and server statistics caching (`.stats_cache.json`) with `--refresh` (`-r`) options.
  - Granular error classification engine (`classify_harvest_error`, `analyze_harvest_errors`, `render_harvest_errors`) categorizing Not Found, Authentication Required, Restricted Study, WAF/Bot Protection, Server Errors, Timeouts, and SSL/TLS diagnostics.
  - Comprehensive harvest error persistence in `.manifest.json` ensuring both transient (recoverable) and permanent failures are captured, queryable via `dartfx-nada errors`, and automatically retried on subsequent runs.
  - Non-recoverable error caching to prevent redundant requests on consecutive harvest runs.
  - Automated API token discovery and persistence via `.api_token`, `.nada_tokens.json`, and environment variables (`NADA_API_TOKEN` / `NADA_{HOST}_API_TOKEN`).
- **Server Discovery & Registry Subsystem**:
  - Centralized `ServerInstallation` Pydantic model with ISO 3166-1 Alpha-2 `country_code` derivation, `clean_hostname`, and `url` helper properties.
  - Comprehensive ISO 3166-1 country crosswalk (`COUNTRY_TO_ISO2`, `get_iso2_code`, `matches_country`).
  - `fetch_nada_installations` registry engine with remote/local fallback and dynamic synthesis for unlisted/private servers.
  - Active clickable hyperlinks and CSV/JSON output formatting in `dartfx-nada list` (with `installations` supported as backwards-compatible alias).
- **Core Server Client (`NadaServer`)**:
  - Full Pydantic v2 model inheriting from `BaseModel`.
  - Automatic version detection (`detect_server_version`) dynamically handling NADA v5 (`/api/`) and NADA v4 (`/index.php/api/`) endpoint route prefixes.
  - Integrated `requests-cache` session management.
  - Study export methods (`get_study_ddi`, `get_study_json`, `get_study_resources`, `get_study_croissant`, `get_study_schema`).
  - Search query validation via `SearchParameters` Pydantic model.
  - Transparent HTTP fallback in `NadaServer.request()` when encountering `requests.exceptions.SSLError` on HTTPS endpoints.
- **Server Statistics & Diagnostics Subsystem** (`dartfx-nada stats`):
  - Multi-threaded concurrent query fetching via `ThreadPoolExecutor` for low response latency.
  - Semantic color-coded performance tier badges (`⚡ <3s Fast`, `🟢 3–8s Normal`, `🟡 8–15s Moderate`, `🔴 >15s Degraded`, `🔒 Protected`, `❌ Failed`).
  - Deep SSL/TLS diagnostics isolating missing intermediate CA chains, expired certificates, hostname mismatches, and self-signed certificates.
  - Abbreviated, clean status error badges (e.g. `SSL Hostname Mismatch`, `Connection Failure`, `Timeout (10s)`) replacing verbose connection pool stack traces in table output.
  - Domain parking / domain-for-sale detection and strict JSON/CSV payload validation in server stats probing, preventing false-positive "Online" classifications on parked or non-NADA domains.
  - Streamlined stats table layout by removing the redundant Collections column in favor of Studies, Latency, and Status.
  - Fixed study count extraction for NADA v4 servers (e.g., `nada.stat.cm`, `microdata.statistics.gov.rw`) by parsing catalog search count containers in catalog HTML, resolving 0-study reporting on active v4 instances.
  - Bidirectional HTTP/HTTPS protocol resilience: Automatic fallback to HTTP on SSL verification failures with recovered study counts (`HTTP (SSL Failed)`), and HTTPS availability probes for legacy HTTP servers with upgrade recommendations (`Online (HTTPS Avail)`).
  - Comprehensive Server Status & Diagnostics documentation in `IMPLEMENTATION.md`, `README.md`, and Sphinx docs (`docs/source/diagnostics.md`), detailing status taxonomy, parked domain heuristics, and the technical discrepancy between web browser AIA auto-healing and OpenSSL/Python SSL incomplete chain validation.
- **Modern CLI & Tooling Architecture**:
  - Full-featured CLI powered by Typer and Rich with smart dash-to-underscore option normalization.
  - CLI commands: `list` (preferred command for server registry discovery; alias: `installations`), `info`, `collections`, `search`, `study`, `harvest`, `stats`, `errors`.
  - Enriched `dartfx-nada info` command displaying organization name, catalog URL, detected version, live/cached total study count, collections count, protocol health advisory, and a comprehensive Supported Metadata & Export Formats matrix.
  - Added support for MLCommons Croissant (`--export croissant`) and Google Dataset Search Schema.org JSON-LD (`--export schema`) in `dartfx-nada study` and metadata harvester subsystem (`--formats schema`).
  - Comprehensive unit test suite covering all core modules, CLI commands, and harvester workflows.

### Removed

- Removed deprecated legacy `NadaCatalog`, `NadaStudy`, and `NadaHarvester` classes/aliases, establishing `NadaServer` and `ServerHarvester` as the streamlined canonical interfaces.

## [0.1.0] - 2024-03-01

### Added

- Initial release of the NADA toolkit.
- Basic `NadaServer`, `NadaCatalog`, and `NadaStudy` classes.
- Basic search and study metadata retrieval.
