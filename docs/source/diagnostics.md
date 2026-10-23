# Server Status & Diagnostics Guide

The `dartfx-nada stats` command monitors live health, responsiveness, and catalog statistics across IHSN NADA repositories worldwide.

## Status Indicators & Health Badges

The **Status** column classifies repository endpoints into semantic, actionable diagnostics:

| Status Badge | Category | Availability Tier | Description |
| :--- | :--- | :--- | :--- |
| `Online` | Operational | Accessible | Valid NADA API server responded with study counts. |
| `Cached (24h)` | Cache Hit | Accessible | Metrics loaded from local `.stats_cache.json` within the 24-hour TTL. Use `--refresh` to bypass. |
| `Online (AIA Fixed)` | Operational / Repaired | Accessible | Missing intermediate CA certificate fetched and verified via Authority Information Access (AIA). |
| `Online (HTTPS Avail)` | Operational / Advisory | Accessible | Server is currently reached over HTTP, but HTTPS is fully functional and recommended. |
| `HTTP (SSL Failed)` | Fallback | Accessible | Primary HTTPS failed SSL certificate verification, but recovered via HTTP port 80. |
| `Auth Required` / `401` | Access | Restricted | Server is active but catalog endpoints require authentication. |
| `Forbidden (403)` | Access | Restricted | Server rejected the request due to access control or client filtering. |
| `Cloudflare Challenge` | Access | Restricted | Cloudflare Turnstile or JavaScript bot challenge intercepted API requests. |
| `Perimeter Block (Geo/IP)` | Access | Restricted | National or institutional perimeter firewall blocks access from external IP ranges. |
| `WAF / Bot Protection` | Access | Restricted | Request was blocked by AWS WAF, DDoS-Guard, or automated protection rules. |
| `SSL Incomplete Chain` | SSL/TLS | Offline / Error | The server serves its leaf certificate without intermediate CA certificates, and HTTP port 80 is closed. |
| `SSL Hostname Mismatch` | SSL/TLS | Offline / Error | The certificate Subject Alternative Name (SAN) does not match the configured domain. |
| `SSL Expired` | SSL/TLS | Offline / Error | The server's SSL/TLS certificate has expired. |
| `SSL Untrusted / Self-Signed` | SSL/TLS | Offline / Error | Certificate signed by an unknown or self-signed authority not present in Mozilla's trust store. |
| `Domain For Sale` | Inactive | Offline / Inactive | The domain registration has lapsed and redirects to a domain broker or registrar parking page (e.g. Sedo, GoDaddy, Dan.com). |
| `Catalog Retired (410)` | Inactive | Offline / Inactive | Catalog decommissioned or retired by institution. |
| `Not NADA Server` | Inactive / Replaced | Offline / Inactive | Endpoint returns HTTP 200/HTML or redirects, but does not provide valid NADA API responses. |
| `Not Found (404)` | Network | Offline / Error | Catalog API endpoints not found on host. |
| `DNS Resolution Failure` | Network | Offline / Error | Domain name cannot be resolved by DNS (`[Errno 8] nodename nor servname provided, or not known`). |
| `Timeout (10s)` | Network | Offline / Error | Connect or read timeout exceeded the 10-second limit. |
| `Connection Refused` | Network | Offline / Error | Remote server rejected the TCP connection on port 80/443. |
| `Service Unavailable (503)` | Server | Offline / Error | Backend service temporarily unavailable or under maintenance. |
| `Server Error (500)` | Server | Offline / Error | Backend application error or unhandled PHP exception. |

---

## Availability Tiers & Summary Metrics

The summary at the bottom of `dartfx-nada stats` differentiates repositories into three clear availability tiers:

1. **Accessible** (`green`): Actively responding NADA installations whose study catalog and metadata can be freely browsed and harvested.
2. **Restricted** (`yellow`): Active hosts/installations where access is blocked by perimeter firewalls (e.g. BPS Indonesia), Cloudflare bot challenges (e.g. Pacific Data Hub, PSA Philippines), or API authentication tokens.
3. **Offline / Inactive** (`red`): Endpoints that are dead, unregistered/for sale, retired, timing out, or failing DNS/connection/TLS.

**Example Summary Caption:**
```text
Total: 38 accessible • 7 restricted • 30 offline/inactive (75 servers) • 67,936 studies indexed
```

---

## Bidirectional Protocol Handling & AIA Resilience

`dartfx-nada` features automated cryptographic resilience and protocol intelligence across both the CLI (`stats`, `info`) and core library (`NadaServer`):

### 1. AIA Intermediate CA Certificate Chasing (HTTPS Repaired)
When a server has an incomplete certificate chain (missing intermediate CA certificate):
1. Standard Python and OpenSSL libraries fail with `SSLCertVerificationError: unable to get local issuer certificate`.
2. Rather than dropping security or disabling certificate verification (`verify=False`), `dartfx-nada` implements RFC 5280 **Authority Information Access (AIA) chasing**.
3. It inspects the server's leaf certificate DER bytes to locate the `CA Issuers` URI (e.g. `http://secure.globalsign.com/cacert/...crt` or Let's Encrypt / Sectigo PKI endpoints).
4. It downloads the missing intermediate certificate, converts it from DER to PEM, and appends it to Mozilla's root CA trust bundle (`certifi`).
5. It caches the augmented bundle in `~/.cache/dartfx/nada/ca_bundles/{host}.pem`.
6. Subsequent HTTPS connections verify the full chain cryptographically against the root authority, reporting status as **`Online (AIA Fixed)`**.

### 2. SSL Failure -> HTTP Fallback
If intermediate CA chasing cannot repair the SSL connection (for example, if the certificate is self-signed, expired, or hostname mismatched), `dartfx-nada`:
1. Captures the specific SSL error (e.g., `SSL Expired` or `SSL Hostname Mismatch`).
2. Probes `http://` on port 80.
3. If the HTTP endpoint is active and serves valid NADA catalog data, the server's study counts are recovered, the status is flagged as **`HTTP (SSL Failed)`**, and `NadaServer.request()` falls back to HTTP automatically.
4. If port 80 is closed, firewalled, or strictly redirects back to HTTPS, the server retains its exact SSL diagnostic status (e.g., `SSL Incomplete Chain` or `SSL Expired`).

### 3. HTTP Server -> HTTPS Upgrade Probing
Many historical NADA registries contain legacy `http://` URLs. If a server is queried over HTTP:
1. `dartfx-nada` automatically probes `https://` in the background.
2. If the HTTPS endpoint responds with valid NADA responses, valid certificates, and zero SSL errors, `stats` highlights the server as **`Online (HTTPS Avail)`**.
3. `dartfx-nada info` renders an advisory recommending updating the repository URL to `https://`.

---

## Technical Deep-Dive: SSL Incomplete Chain vs. Web Browsers

### Why did some servers show `HTTP (SSL Failed)` while others showed `SSL Incomplete Chain`?

Both servers suffered from the exact same SSL misconfiguration: an incomplete certificate chain (`unable to get local issuer certificate`).
However, their fallback behavior differed:
- **`anads.ansd.sn` (ANSD Senegal)**: Had an open, responsive unencrypted HTTP service on port 80. When initial HTTPS failed, `dartfx-nada`'s fallback recovered the catalog via HTTP port 80 and reported `HTTP (SSL Failed)`.
- **`censusindia.gov.in` (Census Digital Library, India)**: Had its HTTP port 80 closed, timing out, or not serving the catalog. Because HTTP fallback failed, the server reported the raw SSL failure `SSL Incomplete Chain`.

With the introduction of **AIA Intermediate CA Chasing**, both servers now automatically repair their certificate chain, connect securely over verified HTTPS, and report **`Online (AIA Fixed)`**!

### The Server Configuration Error
When an SSL/TLS certificate is issued by a Certificate Authority (such as Let's Encrypt, Sectigo, GlobalSign, or DigiCert), it sits at the bottom of a trust hierarchy:
1. **Root CA Certificate** (installed in operating system trust stores)
2. **Intermediate CA Certificate** (signs the customer's certificate)
3. **Leaf / Server Certificate** (the specific domain)

Web server software (Nginx, Apache, IIS) must be configured to send the **full certificate chain** (the leaf certificate followed by the intermediate CA certificates). If a server administrator configures only the leaf certificate (`cert.pem`) instead of the bundled chain (`fullchain.pem`), the server sends an **incomplete chain**.

### How Server Administrators Resolve Incomplete Chains
Server administrators can resolve this issue permanently by configuring the full certificate chain:
- **Nginx**: Use `ssl_certificate /path/to/fullchain.pem;` (not `cert.pem`).
- **Apache**: Use `SSLCertificateFile /path/to/cert.pem` and `SSLCertificateChainFile /path/to/chain.pem` (or combined in Apache 2.4.8+).
- **Microsoft IIS**: Import the intermediate CA certificate into the Windows Server `Local Computer -> Intermediate Certification Authorities` certificate store so IIS bundles it in the TLS handshake.

---

## Non-NADA Servers & Parked Domains

Over time, several historical NADA catalog domains have changed ownership or were replaced by general institutional portals:

- **Parked Domain Detection**: `dartfx-nada` automatically checks HTTP responses for domain-for-sale signatures (such as Sedo parking, Dan.com, GoDaddy parking, or text patterns like "Buy this domain"). These are marked as `Domain For Sale`.
- **Strict Payload Verification**: To prevent false positives where an unrelated site returns an HTTP 200 HTML page for API paths, `dartfx-nada` inspects payloads for valid NADA catalog JSON schemas or NADA v4 CSV export headers (`idno,title,nation`).

---

## Performance Latency Ratings

Response times for server checks are classified into color-coded performance tiers:

| Tier | Latency | Badge | Meaning |
| :--- | :--- | :--- | :--- |
| **Fast** | `< 3.0s` | `⚡ 1.2s` | Excellent API responsiveness |
| **Normal** | `3.0s – 8.0s` | `🟢 4.5s` | Healthy catalog response time |
| **Moderate** | `8.0s – 15.0s` | `🟡 9.2s` | High server load or geographic latency |
| **Degraded** | `> 15.0s` | `🔴 18.0s` | Severe performance degradation or near-timeout |
| **Protected** | N/A | `🔒` | Authentication or WAF protection |
| **Failed** | N/A | `❌ 10.0s` | Connection failure or error |

---

## Caching Behavior

1. **24-Hour Cache**: By default, `dartfx-nada stats` caches successful server queries for 24 hours in `.stats_cache.json` under `./.cache/` or the designated repository directory.
2. **Auto-Invalidation of Suspicious Records**: If a cached entry claims `is_nada=True` but reports 0 studies (and no API token is present), `dartfx-nada` automatically ignores the stale cache and re-verifies the server live.
3. **Bypassing Cache**: Pass `--refresh` to force live queries across all servers:
   ```bash
   dartfx-nada stats --refresh
   ```
