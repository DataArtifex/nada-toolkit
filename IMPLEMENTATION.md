# Technical Implementation

This document describes the technical architecture and implementation details of `dartfx-nada`.

## Architecture Overview

The toolkit is designed to be a high-level, type-safe wrapper around the IHSN NADA (National Data Archive) Catalog and API services. It prioritizes discovery, catalog browsing, and resilient metadata harvesting.

### Core Components

1.  **`NadaServer`**: The primary interface for API interactions.
    - Inherits from `pydantic.BaseModel` for configuration and state validation.
    - Manages `requests-cache` sessions for network performance and cache transparency.
    - Automatic version detection (`detect_server_version`) identifying NADA v5 (`/api/`) vs v4 (`/index.php/api/`) endpoint prefixes.
    - Provides high-level methods for Catalog Search, Collection browsing, Country/Topic codes, and Study exports (`ddi`, `json`, `resources`, `croissant`, `schema`).
2.  **`ServerInstallation`**: A Pydantic model representing a NADA repository installation with rich metadata (name, hostname, coordinates, launch year, ISO 3166-1 alpha-2 `country_code`, description, etc.) and helper properties (`clean_hostname`, `url`).
3.  **`SearchParameters`**: A comprehensive Pydantic model validating NADA Catalog Search API query parameters (pagination, keyword searches, collection/country/topic filters, date ranges, and sorting).
4.  **`fetch_nada_installations` & Registry Engine**: The centralized registry subsystem for discovering worldwide NADA instances.
    - Multi-source registry loading (remote or local CSV/JSON endpoints with configurable timeout).
    - Built-in ISO 3166-1 Alpha-2 country crosswalk (`COUNTRY_TO_ISO2`, `get_iso2_code`, `matches_country`).
    - Filtering by hostname, ISO country code, or country substring.
    - Automatic fallback synthesis for unlisted, private, or development NADA deployments.
5.  **`ServerHarvester` / Metadata Harvester**: A resilient bulk metadata synchronization subsystem.
    - Consumes the unified `fetch_nada_installations` registry engine.
    - **Concurrent Multi-Threaded Execution**: Multi-threaded harvesting engine powered by `ThreadPoolExecutor` (`--workers` / `-w`, default: 4) with thread-safe manifest tracking, error recording, file writes, and Rich progress visualization backed by `threading.Lock()`.
    - Multi-format harvesting: DDI 2.5 CodeBook XML (`ddi`), Study Metadata JSON (`json`), External Resources & Documentation (`resources`), MLCommons Croissant JSON-LD (`croissant`), and Google Dataset Search Schema.org JSON-LD (`schema`).
    - **Study-Level Packaging Granularity**: Exports are fetched per-study from NADA endpoints (`/catalog/ddi/{idno}`, `/catalog/json/{idno}`, `/catalog/resources/{idno}`, `/catalog/croissant/{idno}`, and landing page Schema.org extraction).
    - Incremental diff synchronization (Additions, Updates, Unchanged, Deletions) backed by `.manifest.json`.
    - Fast timestamp checking and SHA-256 integrity verification.
    - 24-hour catalog (`.catalog_cache.json`) and server statistics (`.stats_cache.json`) caching.
    - Automated API token discovery and resolution (`.api_token`, `.nada_tokens.json`, environment variables).
    - **Intelligent Error Classification Engine** (`classify_harvest_error`): Categorizes failure reasons into distinct taxonomies (Study Not Found HTTP 404, Authentication Required HTTP 401, Restricted Study HTTP 403, WAF / Bot Protection HTTP 403, Server Errors HTTP 5xx, Network Timeouts, Parse Errors, and SSL/TLS Diagnostics).
6.  **Server Statistics, Performance Latency & Diagnostics Subsystem**:
    - **Concurrent Metric Queries**: Queries study counts in parallel via `ThreadPoolExecutor` for low response latency.
    - **Performance Tier Rating Engine** (`format_response_latency`): Classifies response times into semantic color-coded tiers (`⚡ <3s` Fast, `🟢 3–8s` Normal, `🟡 8–15s` Moderate, `🔴 >15s` Degraded, `🔒` Protected, `❌` Failed).
    - **Deep SSL/TLS Diagnostics** (`classify_harvest_error`, `abbreviate_error`): Inspects peer certificates and verification error chains to isolate incomplete intermediate CA chains, certificate expirations, hostname mismatches, and self-signed certificates.
    - **Parked & Non-NADA Server Filtering**: Distinguishes valid NADA installations from domain registrars, domain-for-sale parking pages, and unrelated web portals returning HTTP 200 HTML.
    - **24-Hour Persistence & Auto-Invalidation**: Stores metrics in `.stats_cache.json` under the server directory (or `.cache/`) for sub-second CLI re-rendering. Automatically invalidates and re-queries suspicious zero-study cache records.

### Server Status & Health Diagnostics Taxonomy

The `dartfx-nada stats` command reports concise, color-coded health badges in the **Status** column:

| Status Badge | Category | Technical Cause & Meaning |
| :--- | :--- | :--- |
| `Online` | Operational | Valid NADA API server responded with study counts. |
| `Cached (24h)` | Cache Hit | Results loaded from local `.stats_cache.json` within the 24-hour TTL. |
| `Online (HTTPS Avail)` | Operational / Advisory | Server is queried over HTTP, but HTTPS is fully functional and recommended. |
| `HTTP (SSL Failed)` | Fallback | Primary HTTPS failed SSL certificate verification, but study counts were recovered via HTTP port 80. |
| `SSL Incomplete Chain` | SSL/TLS | The server serves only its leaf certificate without the intermediate CA bundle (*see detailed explanation below*). |
| `SSL Hostname Mismatch` | SSL/TLS | The certificate Subject Alternative Name (SAN) does not match the configured domain. |
| `SSL Expired` | SSL/TLS | The server's SSL/TLS certificate has passed its expiration date. |
| `SSL Untrusted / Self-Signed` | SSL/TLS | The certificate was issued by an untrusted or self-signed authority not in Mozilla's root store. |
| `Domain For Sale` | Inactive | The domain has expired and redirects to a registrar parking/auction page (e.g., Sedo, GoDaddy, Dan.com). |
| `Not NADA Server` | Inactive/Other | The server returns HTTP 200 or an HTML splash page, but fails NADA API verification (not running NADA v4 or v5). |
| `DNS Resolution Failure` | Network | Domain name resolution failed (`[Errno 8] nodename nor servname provided, or not known`). |
| `Timeout (10s)` | Network | TCP connection or HTTP response read exceeded the 10-second diagnostic timeout limit. |
| `Connection Refused` | Network | Remote server actively refused the TCP connection on port 80/443. |
| `401 Unauthorized` / `403 Forbidden` | Access | Server is online but catalog endpoints require an authenticated API key or token. |
| `WAF / Cloudflare Block` | Access | Request was blocked by a Web Application Firewall, Cloudflare challenge, or bot protection. |

### Bidirectional Protocol Handling & Resilience

The diagnostic engine and `NadaServer` implement bidirectional protocol intelligence:
1. **SSL Error -> HTTP Fallback**: When an HTTPS server fails SSL certificate validation (e.g., incomplete intermediate CA chain, expired cert, or hostname mismatch), `dartfx-nada` automatically probes `http://`. If HTTP is functional, it recovers catalog study counts, records the badge as `HTTP (SSL Failed)`, and preserves the underlying SSL diagnostic reason for user inspection. `NadaServer.request()` similarly catches `requests.exceptions.SSLError` and retries against HTTP while logging an advisory notice.
2. **HTTP Server -> HTTPS Upgrade Probing**: When a server is registered over legacy `http://`, the engine probes `https://`. If HTTPS is fully functional with a valid certificate, `stats` highlights the server as `Online (HTTPS Avail)`, and `dartfx-nada info` renders an advisory recommending upgrading the URL to HTTPS.

### Technical Deep-Dive: SSL Incomplete Chain vs. Web Browsers

A common discrepancy encountered during diagnostics is when a server (e.g., `https://anads.ansd.sn` or `https://catalog.ihsn.org`) shows **`SSL Incomplete Chain`** in `dartfx-nada`, yet opens without any warning in a web browser (Chrome, Safari, Firefox, Edge).

#### Root Cause: Missing Intermediate CA Bundle
1. An SSL/TLS certificate chain consists of:
   - **Leaf (Site) Certificate**: Issued to the domain.
   - **Intermediate CA Certificate(s)**: Issued by a trusted Root CA to intermediate signing authorities.
   - **Root CA Certificate**: Stored directly in the client operating system or trust store (e.g., Mozilla CA store used by `certifi`).
2. Web servers (Nginx, Apache, IIS) must be configured to send the **full certificate chain** (`fullchain.pem` in Let's Encrypt, or server cert + intermediate cert bundle).
3. If an administrator configures only the leaf certificate (`cert.pem`), the server sends an **incomplete chain**.

#### Why Python / CLI Tools Fail
- Python (`requests`, `urllib3`, `ssl`) verifies certificates via OpenSSL against the local root CA store (`certifi`).
- Because root CA stores intentionally do **not** store thousands of dynamic intermediate CAs, OpenSSL cannot connect the leaf certificate to any trusted root.
- OpenSSL strictly rejects the connection with:
  `SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate`.

#### Why Modern Browsers Still Work
Browsers employ two automatic resilience mechanisms:
1. **AIA Fetching (Authority Information Access)**: The leaf certificate contains an X.509 extension with a direct HTTP URL to download the missing intermediate certificate (e.g., `http://r3.i.lencr.org/...`). When a browser detects an incomplete chain, it fetches the intermediate certificate in the background and validates the chain.
2. **Intermediate Certificate Caching**: Browsers cache all valid intermediate CAs they have encountered from other sites. If any previously visited site used the same CA, the browser already has the intermediate certificate locally and completes the chain transparently.

Python and OpenSSL do not perform AIA fetching by default for performance and security reasons.

### Non-NADA Servers & Parked Domain Detection

Many historical statistical agency registries include URLs that have since lapsed, been acquired by domain squatters, or replaced by generic national portals:
- **Parked Domain Detection**: Inspects response URLs, titles, and body content for known parking signatures (`sedoparking.com`, `dan.com`, `godaddy.com/park`, `domain for sale`, `buy this domain`).
- **Strict Payload Verification**: To avoid false positives where an unrelated site returns HTTP 200 HTML for `/api/catalog/search` or `/catalog/export/csv`, the diagnostic engine strictly verifies that the payload contains valid NADA JSON schemas or valid v4 CSV export headers (`idno,title,nation`).


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
