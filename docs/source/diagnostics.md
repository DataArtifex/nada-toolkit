# Server Status & Diagnostics Guide

The `dartfx-nada stats` command monitors live health, responsiveness, and catalog statistics across IHSN NADA repositories worldwide.

## Status Indicators & Health Badges

The **Status** column classifies repository endpoints into semantic, actionable diagnostics:

| Status Badge | Category | Description |
| :--- | :--- | :--- |
| `Online` | Operational | Valid NADA API server responded with study counts. |
| `Cached (24h)` | Cache Hit | Metrics loaded from local `.stats_cache.json` within the 24-hour TTL. Use `--refresh` to bypass. |
| `Online (HTTPS Avail)` | Operational / Advisory | Server is currently reached over HTTP, but HTTPS is fully functional and recommended. |
| `HTTP (SSL Failed)` | Fallback | Primary HTTPS failed SSL certificate verification, but recovered via HTTP port 80. |
| `SSL Incomplete Chain` | SSL/TLS | The server serves its leaf certificate without intermediate CA certificates. |
| `SSL Hostname Mismatch` | SSL/TLS | The certificate Subject Alternative Name (SAN) does not match the configured domain. |
| `SSL Expired` | SSL/TLS | The server's SSL/TLS certificate has expired. |
| `SSL Untrusted / Self-Signed` | SSL/TLS | Certificate signed by an unknown or self-signed authority not present in Mozilla's trust store. |
| `Domain For Sale` | Inactive | The domain registration has lapsed and redirects to a domain broker or registrar parking page (e.g. Sedo, GoDaddy, Dan.com). |
| `Not NADA Server` | Inactive / Replaced | Endpoint returns HTTP 200/HTML or redirects, but does not provide valid NADA API responses. |
| `DNS Resolution Failure` | Network | Domain name cannot be resolved by DNS (`[Errno 8] nodename nor servname provided, or not known`). |
| `Timeout (10s)` | Network | Connect or read timeout exceeded the 10-second limit. |
| `Connection Refused` | Network | Remote server rejected the TCP connection on port 80/443. |
| `401 Unauthorized` / `403 Forbidden` | Access | Server is active but catalog endpoints require authentication. |
| `WAF / Cloudflare Block` | Access | Request was blocked by a Web Application Firewall, Cloudflare challenge, or bot protection rule. |

---

## Bidirectional Protocol Handling & Resilience

`dartfx-nada` features bidirectional protocol intelligence across both the CLI (`stats`, `info`) and core library (`NadaServer`):

### 1. SSL Failure -> HTTP Fallback
When a server has broken SSL/TLS configuration (such as missing intermediate certificates or hostname mismatches), Python and OpenSSL reject the HTTPS connection. Rather than failing outright, `dartfx-nada`:
1. Captures the specific SSL error (e.g., `SSL Incomplete Chain`).
2. Automatically probes `http://` on port 80.
3. If the HTTP endpoint is active and serves valid NADA catalog data, the server's study counts are recovered, the status is flagged as **`HTTP (SSL Failed)`**, and `NadaServer.request()` falls back to HTTP automatically.
4. The exact SSL root cause is preserved and reported in `dartfx-nada info` and diagnostic logs.

### 2. HTTP Server -> HTTPS Upgrade Probing
Many historical NADA registries contain legacy `http://` URLs. If a server is queried over HTTP:
1. `dartfx-nada` automatically probes `https://` in the background.
2. If the HTTPS endpoint responds with valid NADA responses, valid certificates, and zero SSL errors, `stats` highlights the server as **`Online (HTTPS Avail)`**.
3. `dartfx-nada info` renders an advisory recommending updating the repository URL to `https://`.

---

## Technical Deep-Dive: SSL Incomplete Chain vs. Web Browsers

### Why does a server show `SSL Incomplete Chain` in the CLI but load fine in a web browser?

This is one of the most common issues observed when working with national statistical agency servers.

#### The Server Configuration Error
When an SSL/TLS certificate is issued by a Certificate Authority (such as Let's Encrypt, Sectigo, or DigiCert), it sits at the bottom of a trust hierarchy:
1. **Root CA Certificate** (installed in operating system trust stores)
2. **Intermediate CA Certificate** (signs the customer's certificate)
3. **Leaf / Server Certificate** (the specific domain)

Web server software (Nginx, Apache, IIS) must be configured to send the **full certificate chain** (the leaf certificate followed by the intermediate CA certificates). If a server administrator configures only the leaf certificate (`cert.pem`) instead of the bundled chain (`fullchain.pem`), the server sends an **incomplete chain**.

#### Why Python / CLI Tools Fail
Standard command-line utilities and libraries (`requests`, `urllib3`, `curl`, OpenSSL) only have access to root CA certificates (via Mozilla's `certifi` bundle). When the server fails to provide the intermediate certificate, OpenSSL cannot link the server's certificate back to a trusted root, causing:
```text
SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate
```

#### Why Web Browsers Tolerate It
Modern web browsers (Google Chrome, Mozilla Firefox, Apple Safari, Microsoft Edge) implement automated repair mechanisms that conceal this misconfiguration:
- **Authority Information Access (AIA) Chasing**: The leaf certificate contains an X.509 extension specifying an HTTP URL where the intermediate certificate can be fetched. When a browser detects a missing intermediate certificate, it fetches it in the background to complete the chain.
- **Intermediate Certificate Caching**: Browsers cache valid intermediate certificates encountered on any previously visited website. If any other website shared the same intermediate CA, the browser uses the cached certificate silently.

Because CLI clients and automated harvesters do not perform background AIA chasing, `dartfx-nada` highlights this as **`SSL Incomplete Chain`**.

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
