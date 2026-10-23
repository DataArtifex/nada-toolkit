# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.1] - 2026-10-22

### Added

- **Authority Information Access (AIA) Intermediate CA Certificate Chasing** (`dartfx-nada stats`, `NadaServer`):
  - Added automated RFC 5280 Authority Information Access (AIA) intermediate CA certificate fetching via `resolve_aia_intermediate_ca()` and `get_ca_bundle_for_host()`.
  - When servers serve incomplete TLS certificate chains (missing intermediate CA certs, causing `SSLCertVerificationError: unable to get local issuer certificate`), `dartfx-nada` automatically inspects the leaf certificate's DER payload, extracts CA Issuers URIs, downloads the missing intermediate certificate(s), and augments Mozilla's `certifi` CA trust bundle.
  - Caches combined PEM bundles locally in `~/.cache/dartfx/nada/ca_bundles/{host}.pem` for fast, persistent cryptographic verification across subsequent requests.
  - Successfully recovers and unlocks servers with incomplete chains over HTTPS (such as `anads.ansd.sn` in Senegal and `censusindia.gov.in` in India with 40,273 studies) without compromising TLS verification or falling back to unencrypted HTTP.
  - Added new status indicator `Online (AIA Fixed)` in `dartfx-nada stats` table, JSON, and Markdown outputs, and detailed protocol/certificate chain diagnostics in `dartfx-nada info`.
- **PHP Version & Server Environment Detection** (`dartfx-nada stats`, `dartfx-nada info`):
  - Passive extraction of PHP runtime version and web server banner via `extract_server_environment()` from standard HTTP response headers (`X-Powered-By`, `Server`, `X-PHP-Version`).
  - Color-coded security lifecycle formatting in `format_php_version()` highlighting active vs. end-of-life runtimes: green for PHP >= 8.2 (supported), yellow for PHP 8.0–8.1 (EOL), bold red for PHP < 8.0 (Critical EOL), and dim red `~5.x-7.x` inference for legacy NADA v4 instances when banners are hidden.
  - Added dedicated `PHP` column to the `dartfx-nada stats` table display.
  - Added `--show-env` (`-e`) CLI option to `dartfx-nada stats` to reveal extended server banners (e.g. Apache, Nginx, LiteSpeed, Cloudflare).
  - Enriched `dartfx-nada info` to display detected PHP Runtime and Web Server.
  - Cached detected PHP version and web server banner in `.stats_cache.json` for 24-hour persistence.
- **Default Server Name Sorting & Custom `--sort` Options** (`dartfx-nada stats`):
  - Results in `dartfx-nada stats` are now sorted alphabetically by server/repository name A–Z by default, resolving arbitrary multithreaded completion order.
  - Added `--sort` (`-S`) option to sort by `name` (default), `country`, `studies`, `latency`, `php`, or `status`.
  - Added `--desc` and `--asc` flags to control sort direction, defaulting to descending for study counts and ascending for names/latency.
- **JSON, Markdown & CSV Output Options & Direct File Export** (`dartfx-nada stats`):
  - Added `--format` (`-f`) option accepting `table` (default), `json`, `markdown` (or `md`), and `csv`.
  - Added convenient `--json` flag to print structured machine-readable JSON array of server statistics.
  - Added convenient `--markdown` / `--md` flags to render a clean GitHub-flavored Markdown table with clickable server links and response badges.
  - Added `-o` / `--output` option to write statistics directly to a file instead of console. When invoked as a bare flag (`-o` or `--output`), automatically writes to standard filenames based on the chosen format: `dartfx-nada.stats.txt`, `dartfx-nada.stats.json`, `dartfx-nada.stats.md`, or `dartfx-nada.stats.csv`. Custom file paths (e.g. `-o my_stats.json`) are also supported.
  - Routed progress bars to `stderr` with `transient=True` so stdout redirection (e.g. `dartfx-nada stats --json > stats.json`) outputs 100% clean, parseable data.
- **Comprehensive Failure Diagnostics & Reporting for Unresolvable/Restricted Servers** (`dartfx-nada stats`, `ServerHarvester`):
  - Refined probe failure diagnostics in `_diagnose_probe_error()` and `classify_harvest_error()` to pinpoint exact failure modes rather than defaulting to generic `Not a NADA server` or `Forbidden (403)`:
    - `Perimeter Block (Geo/IP)`: Identifies perimeter firewalls and geo-fencing (e.g., BPS Indonesia's `Perimeter URL Block`).
    - `Cloudflare Challenge`: Identifies interactive Cloudflare Turnstile / JavaScript bot challenges (e.g., Pacific Data Hub, Sirusa).
    - `Bot Protection (WAF)`: Identifies AWS WAF, DDoS-Guard, Imperva, and other automated protection layers.
    - `Auth Required (401)`: Identifies catalogs or search endpoints requiring authentication tokens.
    - `Catalog Retired (410)`: Identifies decommissioned archives (e.g., ILO Survey Catalog Retired).
    - `Service Unavailable (503)`, `Bad Gateway (502)`, `Server Error (500)`, and `PHP Server Error`: Detects backend downtime without misclassifying legitimate NADA installations as non-NADA.
    - `Not Found (404)`: Distinguishes missing endpoints from non-NADA software.
    - `Not NADA Server`: Reserved strictly for responsive servers running non-NADA platforms.
  - Semantic color coding in CLI tables: yellow for access restrictions (WAF, Cloudflare Challenge, Perimeter Block, Auth tokens), magenta for retired catalogs and expired domains, dim red for non-NADA/404, and red for server/network/SSL failures.
  - Added operational summary captions and markdown footers (`Total: X/Y online (Z restricted/offline) • N studies indexed`) to provide an immediate overview in `dartfx-nada stats`.

- **Mozilla-Compatible User-Agent Prefix**:
  - Adopted standard `Mozilla/5.0 (compatible; dartfx-nada/{version}; +https://github.com/DataArtifex/nada-toolkit)` User-Agent string across `NadaServer` and `ServerHarvester`.
  - Resolves HTTP `403 Forbidden` blocks enforced by AWS WAF (`awselb/2.0`) and Cloudflare bot filters on installations such as the UNHCR Microdata Library (`microdata.unhcr.org`), while maintaining transparent attribution.
- **Registry Schema Simplification (`data/servers.yaml`)**:
  - Removed redundant `hostname` field from `data/servers.yaml`, treating catalog `url` as the sole source of truth for repository endpoints.
  - Automatically derive server `hostname` and `clean_hostname` in `ServerInstallation` from `url` using standard URL parsing.
  - Preserved backward compatibility for programmatic configurations providing explicit `hostname` values.
- **Preserve Catalog Subpaths & Base URLs**:
  - Fixed an issue where servers hosted on subpaths (e.g. `http://160.242.193.218/nada`, `https://anda.inec.gob.ec/anda`, `https://apps.who.int/healthinfo/systems/surveydata`) had their path stripped when probed.
  - Added `parse_server_url` helper in `harvester.py` and updated `ServerInstallation.url`, `fetch_server_stats`, `fetch_active_studies`, and `fetch_study_record` to preserve custom subpaths and ports.
- **ROR Identifier Storage & Dynamic Link Composition**:
  - Simplified the `ror` property in `data/servers.yaml` to store only the ROR identifier instead of full URLs.
  - Comprehensive audit and correction of all ROR identifiers against the official ROR API (`api.ror.org`), fixing invalid IDs (e.g. Millennium Challenge Corporation `0539hqx70`, WHO `01f80g185`, World Bank `02md09461`, FAO `00pe0tf51`, UNHCR `0437ej039`, INED `02cnsac56`, FORS `00weppy16`, ILO `03tebpn36`, INE Cabo Verde `04d9bp554`, INSTAT Madagascar `05g23w557`, ZamStats Zambia `05n7sxn21`).
  - Added verified ROR identifiers for organizations previously missing them (BPS Indonesia `04tq55h40`, INEI Peru `02e838q34`, CAPMAS Egypt `040wqw555`, Registrar General India `05jhdev04`, LISGIS Liberia `0543zsz75`).
  - Removed invalid/hallucinated ROR placeholders for entities not registered in the official ROR database.
  - Enhanced `Custodian` model with automatic URL-to-identifier normalization and added computed `ror_url` and `ror_uri` properties to compose the full `https://ror.org/<id>` link dynamically.
  - Updated `render_servers_markdown` to compose clickable ROR URLs in `data/servers.md`.

## [0.4.0] - 2026-10-11

### Added

- **Multi-Threaded Harvester Engine** (`dartfx-nada harvest`):
  - Concurrent worker execution (`--workers` / `-w`, default: 4) using `ThreadPoolExecutor` with thread-safe manifest tracking, error recording, file writes, and Rich progress visualization.
  - Stabilized download progress bar layout by anchoring server name on the left, fixing bar/count/timer widths, and placing the active filename on the right-hand side to eliminate visual shifting.
  - Fixed harvest timer display by resetting progress state upon transition from catalog discovery to study synchronization and using `TimeElapsedColumn` for active stopwatch timing.
  - Cumulative catalog cache merging and partial-cache expansion handling, ensuring higher `--limit` requests query live servers while retaining previously cached studies.
  - Comprehensive harvest error persistence in `.manifest.json` ensuring both transient (recoverable) and permanent failures are captured, queryable via `dartfx-nada errors`, and retried on subsequent runs using `--retry` / `--retry-errors`.
  - Added `--retry` / `--retry-errors` CLI option to `dartfx-nada harvest` to force re-attempting studies recorded with previous errors.
  - Server directory names safely exclude port numbers (e.g. `microdata.ubos.org` instead of `microdata.ubos.org:7070`) preventing invalid path characters on Windows and NTFS/FAT filesystems.
  - Automatic fallback for survey identifiers (`idno`) containing forward slashes (`/`): Resolves numeric study IDs and queries `/index.php/metadata/export/{id}/ddi` to bypass CodeIgniter URL routing limitations where slashes break standard REST routes.
  - Robust parsing of string-formatted total study counts returned by some NADA server APIs (e.g. instances like `microdata.ubos.org:7070`), preventing Rich progress bar crashes and infinite search pagination loops.
- **Expanded Metadata Standards**:
  - Added support for MLCommons Croissant (`croissant`) and Google Dataset Search Schema.org (`schema` / `.schema.jsonld`) in `dartfx-nada study` and metadata harvester subsystem (`--formats schema`).
  - New export methods on `NadaServer`: `get_study_croissant()` and `get_study_schema()`.
- **Server Discovery & Registry Improvements**:
  - Migrated NADA server list from remote Google Sheet to a community-maintained `data/servers.yaml` file in `/data` for accessibility by non-developers on GitHub, packaged via wheel force-includes.
  - Simplified YAML schema to use only `country_code`, dynamically deriving country names via ISO-2 crosswalks while omitting volatile `version` and `status` fields in favor of live probing (`dartfx-nada stats`).
  - Added structured `custodian` (name, url, ROR URI), `geocoverage` (level, description, GeoNames URI), and `domains` thematic coverage tags.
  - Synchronized server catalog endpoints with the AGRIS NADA monitoring dashboard (`https://monitor.agris-editor.org/index.php`), adding 6 new national repositories (Cabo Verde, Gambia, Ghana Statistical Service, Guinea, Madagascar, Zambia) and updating modern URLs for Benin, Burkina Faso, Côte d'Ivoire, Liberia, Mali, Nepal, Nigeria, Senegal, Sierra Leone, Tanzania, and Togo.
  - Added `list` command as the preferred command for discovering NADA servers, with `installations` retained as a backwards-compatible alias.
  - Active clickable hyperlinks and CSV/JSON output formatting in `dartfx-nada list`.
  - Added sidecar Markdown registry generator (`data/servers.md`) and CLI command `dartfx-nada registry-md` (`--check`).
  - Integrated sidecar markdown generation into `.pre-commit-config.yaml` and `.github/workflows/test.yml` CI verification.
- **Diagnostic & Resilience Enhancements**:
  - Abbreviated status error badges (e.g. `SSL Hostname Mismatch`, `Connection Failure`, `Timeout (10s)`) replacing verbose connection pool stack traces in `dartfx-nada stats`.
  - Domain parking / domain-for-sale detection and strict JSON/CSV payload validation in server stats probing.
  - Streamlined stats table layout by removing the redundant Collections column in favor of Studies, Latency, and Status.
  - Fixed study count extraction for NADA v4 servers (e.g., `nada.stat.cm`, `microdata.statistics.gov.rw`) by parsing catalog search count containers in catalog HTML.
  - Bidirectional HTTP/HTTPS protocol resilience: Automatic fallback to HTTP on SSL verification failures with recovered study counts (`HTTP (SSL Failed)`), and HTTPS availability probes for legacy HTTP servers with upgrade recommendations (`Online (HTTPS Avail)`).
  - Enriched `dartfx-nada info` command displaying organization name, catalog URL, detected version, live/cached total study count, collections count, protocol health advisory, and a comprehensive Supported Metadata & Export Formats matrix.
  - Comprehensive Server Status & Diagnostics documentation in `IMPLEMENTATION.md`, `README.md`, and Sphinx docs (`docs/source/diagnostics.md`).

## [0.2.0] - 2026-10-10

### Added

- **First-Class Metadata Harvester & Sync Subsystem** (`dartfx-nada harvest`):
  - Incremental, hash-verified synchronization of study metadata across NADA servers.
  - Multi-format harvesting supporting DDI 2.5 CodeBook XML (`ddi`), Study Metadata JSON (`json`), and External Documentation/Resources (`resources`), as well as `--formats all`.
  - SHA-256 integrity verification and fast timestamp checking backed by `.manifest.json`.
  - Deletion pruning protection during filtered sync runs (only prune deletions during full, unconstrained server syncs).
  - 24-hour local catalog caching (`.catalog_cache.json`) and server statistics caching (`.stats_cache.json`) with `--refresh` (`-r`) options.
  - Granular error classification engine (`classify_harvest_error`, `analyze_harvest_errors`, `render_harvest_errors`) categorizing Not Found, Authentication Required, Restricted Study, WAF/Bot Protection, Server Errors, Timeouts, and SSL/TLS diagnostics.
  - Non-recoverable error caching to prevent redundant requests on consecutive harvest runs.
  - Automated API token discovery and persistence via `.api_token`, `.nada_tokens.json`, and environment variables (`NADA_API_TOKEN` / `NADA_{HOST}_API_TOKEN`).
- **Server Discovery & Registry Subsystem**:
  - Centralized `ServerInstallation` Pydantic model with ISO 3166-1 Alpha-2 `country_code` derivation, `clean_hostname`, and `url` helper properties.
  - Comprehensive ISO 3166-1 country crosswalk (`COUNTRY_TO_ISO2`, `get_iso2_code`, `matches_country`).
  - `fetch_nada_installations` registry engine with remote/local fallback and dynamic synthesis for unlisted/private servers.
  - Active clickable hyperlinks and CSV/JSON output formatting in `dartfx-nada installations`.
- **Core Server Client (`NadaServer`)**:
  - Full Pydantic v2 model inheriting from `BaseModel`.
  - Automatic version detection (`detect_server_version`) dynamically handling NADA v5 (`/api/`) and NADA v4 (`/index.php/api/`) endpoint route prefixes.
  - Integrated `requests-cache` session management.
  - Study export methods (`get_study_ddi`, `get_study_json`, `get_study_resources`).
  - Search query validation via `SearchParameters` Pydantic model.
- **Server Statistics & Diagnostics Subsystem** (`dartfx-nada stats`):
  - Multi-threaded concurrent query fetching via `ThreadPoolExecutor` for low response latency.
  - Semantic color-coded performance tier badges (`⚡ <3s Fast`, `🟢 3–8s Normal`, `🟡 8–15s Moderate`, `🔴 >15s Degraded`, `🔒 Protected`, `❌ Failed`).
  - Deep SSL/TLS diagnostics isolating missing intermediate CA chains, expired certificates, hostname mismatches, and self-signed certificates.
- **Modern CLI & Tooling Architecture**:
  - Full-featured CLI powered by Typer and Rich with smart dash-to-underscore option normalization.
  - CLI commands: `installations`, `info`, `collections`, `search`, `study`, `harvest`, `stats`, `errors`.
  - Comprehensive unit test suite covering all core modules, CLI commands, and harvester workflows.

### Removed

- Removed deprecated legacy `NadaCatalog`, `NadaStudy`, and `NadaHarvester` classes/aliases, establishing `NadaServer` and `ServerHarvester` as the streamlined canonical interfaces.

## [0.1.0] - 2024-03-01

### Added

- Initial release of the NADA toolkit.
- Basic `NadaServer`, `NadaCatalog`, and `NadaStudy` classes.
- Basic search and study metadata retrieval.
