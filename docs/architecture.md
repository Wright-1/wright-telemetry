# Architecture

Technical reference for developers and contributors.

---

## System Overview

```mermaid
flowchart LR
    subgraph localLAN [Local LAN]
        Miner1["Miner 1"]
        Miner2["Miner 2"]
        MinerN["Miner N"]
        Collector["Wright Telemetry Collector"]
    end
    subgraph wrightCloud [Wright Fan Cloud]
        API["Wright Fan API"]
        Portal["Customer Portal"]
        Loki["Loki"]
        Grafana["Grafana"]
    end
    Miner1 -->|"Braiins REST"| Collector
    Miner2 -->|"Braiins REST"| Collector
    MinerN -->|"Braiins REST"| Collector
    Collector -->|"Encrypted POST"| API
    Collector -->|"Log push"| Loki
    Loki --> Grafana
    API --> Portal
```

The collector runs on any machine on the same LAN as the mining rigs. It polls each miner's local REST API, encrypts the payload, and POSTs it to the Wright Fan cloud API. Operational logs are shipped to Loki for centralized monitoring.

---

## Project Structure

```
wright_telemetry/
    __init__.py          # package version
    __main__.py          # CLI entry point (argparse)
    config.py            # load/save config + interactive setup wizard
    consent.py           # per-metric consent management
    encryption.py        # AES-256-GCM with HKDF key derivation
    api_client.py        # Wright Fan API HTTP client
    models.py            # dataclass models for all metric types
    scheduler.py         # polling loop with crash recovery
    logging_setup.py     # stdout + rotating file + Loki handler
    service.py           # OS service install (systemd / launchd / schtasks)
    collectors/
        __init__.py
        base.py          # abstract MinerCollector interface
        factory.py       # registry-based factory
        braiins.py       # Braiins OS REST adapter
```

---

## Adapter Pattern

New miner backends are added by subclassing `MinerCollector` and registering with the factory:

```mermaid
classDiagram
    class MinerCollector {
        <<abstract>>
        +authenticate()
        +fetch_identity() MinerIdentity
        +fetch_cooling() CoolingData
        +fetch_hashrate() HashrateData
        +fetch_uptime() UptimeData
        +fetch_hashboards() HashboardData
        +fetch_errors() ErrorData
        +get_fetcher(metric) Callable
    }
    class BraiinsCollector {
        -_session: requests.Session
        -_token: str
        +authenticate()
        +fetch_identity()
        ...
    }
    class VnishCollector {
        <<future>>
    }
    class LuxOSCollector {
        <<future>>
    }
    MinerCollector <|-- BraiinsCollector
    MinerCollector <|-- VnishCollector
    MinerCollector <|-- LuxOSCollector
```

### Adding a New Backend

1. Create `wright_telemetry/collectors/your_backend.py`
2. Subclass `MinerCollector` and implement all abstract methods
3. Decorate the class with `@CollectorFactory.register("your_backend")`
4. Import the module in `__main__.py` so the decorator runs

```python
from wright_telemetry.collectors.base import MinerCollector
from wright_telemetry.collectors.factory import CollectorFactory

@CollectorFactory.register("vnish")
class VnishCollector(MinerCollector):
    def authenticate(self) -> None: ...
    def fetch_identity(self) -> MinerIdentity: ...
    def fetch_cooling(self) -> CoolingData: ...
    def fetch_hashrate(self) -> HashrateData: ...
    def fetch_uptime(self) -> UptimeData: ...
    def fetch_hashboards(self) -> HashboardData: ...
    def fetch_errors(self) -> ErrorData: ...
```

---

## Braiins OS API Endpoints

| Metric     | Endpoint                          | Response (key fields)                                  |
|------------|-----------------------------------|--------------------------------------------------------|
| cooling    | `GET /api/v1/cooling/state`       | `fans[].rpm`, `fans[].target_speed_ratio`, `highest_temperature` |
| hashrate   | `GET /api/v1/miner/stats`         | `miner_stats`, `pool_stats`, `power_stats`             |
| uptime     | `GET /api/v1/miner/details`       | `bosminer_uptime_s`, `system_uptime_s`, `uid`, `serial_number` |
| hashboards | `GET /api/v1/miner/hw/hashboards` | `hashboards[].highest_chip_temp`, `board_temp`, etc.   |
| errors     | `GET /api/v1/miner/errors`        | `errors[].message`, `timestamp`, `error_codes`         |

Authentication: `POST /api/v1/auth/login` with `{"username": "...", "password": "..."}` returns `{"token": "..."}`. The token is sent as `Authorization: Bearer <token>` on subsequent requests. The adapter auto-refreshes on 401.

---

## Encryption Scheme

All telemetry payloads are encrypted before leaving the customer's network.

```mermaid
flowchart TD
    APIKey["Wright Fan API Key (string)"]
    APIKey -->|"HKDF-SHA256<br/>salt: wright-telemetry-v1<br/>info: payload-encryption"| DerivedKey["256-bit AES Key"]
    Plaintext["JSON payload"] --> AESGCM["AES-256-GCM"]
    DerivedKey --> AESGCM
    Nonce["Random 12-byte nonce"] --> AESGCM
    AESGCM --> Wire["Wire format:<br/>{nonce, ciphertext}"]
```

- Key derivation: HKDF-SHA256 with fixed salt `wright-telemetry-v1` and info `payload-encryption`
- Encryption: AES-256-GCM with a random 12-byte nonce per payload
- Wire format: `{"nonce": "<base64>", "ciphertext": "<base64>"}`
- The ciphertext includes the GCM authentication tag (appended by the `cryptography` library)
- The server derives the same key from the stored API key

---

## Wright Fan API Contract

```
POST {wright_api_url}/api/v1/telemetry
Headers:
    Content-Type: application/json
    X-API-Key: {api_key}
    X-Facility-ID: {facility_id}
Body: {"nonce": "...", "ciphertext": "..."}
```

Decrypted payload structure:

```json
{
    "metric_type": "cooling",
    "timestamp": "2026-03-22T12:00:00Z",
    "facility_id": "abc123",
    "miner_identity": {
        "uid": "...",
        "serial_number": "...",
        "hostname": "...",
        "mac_address": "..."
    },
    "data": { }
}
```

The `miner_identity` block is included in every POST so the portal can map fan serial numbers (from QR scans) to specific `(miner_uid, fan_position)` pairs without the collector needing to know about fan serials.

---

## Config File Schema

Location: `~/.wright-telemetry/config.json`

```json
{
    "wright_api_key": "string",
    "wright_api_url": "string (default: https://api.wrightfan.com)",
    "facility_id": "string",
    "poll_interval_seconds": 30,
    "consent": {
        "cooling": false,
        "hashrate": false,
        "uptime": false,
        "hashboards": false,
        "errors": false
    },
    "collector_type": "braiins",
    "miners": [
        {
            "name": "string (friendly label)",
            "url": "string (e.g. http://192.168.1.100)",
            "username": "string (optional)",
            "password_b64": "string (base64, optional)"
        }
    ]
}
```

File permissions are set to `0600` on creation.

---

## Fault Tolerance

### Inner Layer (process stays alive)

- Per-miner, per-metric `try/except` in the poll loop
- A single miner being down or a single API call failing never kills the loop
- Failed metrics are logged and skipped; the next miner/metric proceeds normally

### Outer Layer (crash recovery)

- Top-level `try/except` around the entire run loop in `scheduler.py`
- On unhandled exception: log traceback, wait with exponential backoff (10s, 20s, 40s... max 5 min), restart from scratch (re-auth, re-fetch identities, resume polling)
- Backoff resets to zero after a successful startup

### OS Layer (survive reboots)

| Platform | Mechanism | Restart Policy |
|----------|-----------|----------------|
| Linux    | systemd user service | `Restart=always`, `RestartSec=10` |
| macOS    | launchd LaunchAgent | `KeepAlive=true`, `RunAtLoad=true` |
| Windows  | Task Scheduler | On startup, inner crash recovery handles restarts |

Install: `wright-telemetry --install`, Uninstall: `wright-telemetry --uninstall`

---

## Loki Remote Logging

Operational logs (not telemetry data) are shipped to `logs.wrightfan.com` via Loki's push API. This is always-on and not part of user consent.

- Handler: custom `logging.Handler` subclass in `logging_setup.py`
- Batching: up to 100 entries or 10 seconds, whichever comes first
- Labels: `{job="wright-telemetry", facility_id, collector_version, hostname}`
- Auth: Basic auth, credentials resolved via `WRIGHT_LOKI_AUTH` env var (injected at build time from GitHub Secrets)
- Failure mode: silent drop -- never blocks telemetry collection

---

## Building Locally

```bash
# Install dependencies
pip install -r requirements.txt
pip install pyinstaller

# Run from source
python -m wright_telemetry

# Build a standalone binary
pyinstaller wright-telemetry.spec

# Binary is at dist/wright-telemetry
```

---

## GitHub Actions CI

Four workflows in `.github/workflows/`:

| Workflow | Runner | Purpose |
|----------|--------|---------|
| `braiins-test.yml` | `ubuntu-latest` (Python 3.11) | Runs the Braiins test suite on every PR and push to main |
| `build-linux.yml` | `ubuntu-latest` | Tests + build Linux binary |
| `build-macos.yml` | `macos-latest` | Tests + build macOS binary |
| `build-windows.yml` | `windows-latest` | Tests + build Windows binary |

Each build workflow:
1. Checks out code
2. Sets up Python 3.11
3. Installs dependencies + PyInstaller + test deps
4. **Runs the test suite** (build is blocked if tests fail)
5. Builds with `WRIGHT_LOKI_AUTH` from GitHub Secrets
6. Verifies the binary runs (`--version`)
7. Generates SHA256 checksum
8. Uploads artifact (always)
9. Uploads release asset (on version tags like `v0.1.0`)
