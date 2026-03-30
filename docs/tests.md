# Testing

How to run the test suite and what it covers.

---

## Running Tests Locally

You need Python 3.11+ and a virtual environment. From the project root:

```bash
# Create a venv (skip if you already have one)
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install the project + test dependencies
pip install -r requirements.txt
pip install pytest responses

# Run the full suite
pytest tests/ -v
```

That's it. The tests don't need a real miner, network access, or any config file -- everything is simulated with fixture data.

### Useful pytest flags

```bash
# Run just the Braiins collector tests
pytest tests/test_braiins_collector.py -v

# Run a single test class
pytest tests/test_braiins_collector.py::TestAuthentication -v

# Stop on first failure
pytest tests/ -x

# Show print output (useful for debugging fan RPM events)
pytest tests/ -s
```

---

## What the Tests Cover

### Braiins API Simulator (`test_braiins_collector.py`)

Full regression of `BraiinsCollector` against simulated Braiins OS REST API endpoints. The `responses` library intercepts HTTP calls so no real miner is needed.

| Area | What's tested |
|------|--------------|
| Authentication | Token stored in session, no-credentials skip, HTTP error handling, missing token field |
| 401 Auto-retry | `_get()` re-authenticates and retries on 401 |
| Cooling | Fan list parsing (4 fans), highest temperature |
| Hashrate | miner_stats, pool_stats, power_stats sections |
| Uptime | bosminer_uptime_s, bos_version, hostname |
| Hashboards | 3 boards with temps, chip counts, stats |
| Errors | Error entries with codes, timestamps, components |
| Edge cases | Empty responses, missing fields, HTTP 500, connection errors |

### Model Parsing (`test_models.py`)

Unit tests for every `from_braiins()` factory method on the data models. Each model is tested with:

- Full realistic data (from fixture files)
- Empty data (`{}`)
- Missing keys
- Default value fallbacks

Also covers `MinerIdentity.to_dict()` and `TelemetryPayload.to_dict()` serialization.

### Network Discovery (`test_discovery.py`)

| Test | What happens |
|------|-------------|
| Probe 200 | Braiins miner detected, hostname and MAC extracted |
| Probe 401 | Miner detected (auth required), no details |
| Probe 404 | Not a Braiins miner, returns None |
| Probe timeout | Connection error, returns None |
| IP parsing | Single IP, CIDR /24, CIDR /30, ranges, reversed ranges, invalid input |
| Merge logic | Manual miners win on URL conflict, no duplicates |

### Scheduler (`test_scheduler.py`)

Tests the polling loop and fan monitoring logic without any network calls (uses a stub collector).

- **Poll cycle**: all 5 metrics sent to the API client in one cycle
- **Fault tolerance**: one metric throwing an exception doesn't stop the others
- **Fan RPM off**: detects RPM drop from >0 to 0, emits `off` event
- **Fan RPM on**: detects RPM rise from 0 to >0, closes the drop event with duration
- **Stable RPM**: no events when RPM stays the same
- **Collector factory**: builds Braiins collectors, falls back to default type, raises on unknown type

### Encryption (`test_encryption.py`)

- Encrypt then decrypt returns the original payload
- Nested/complex data round-trips correctly
- Different API keys produce different ciphertext
- Wrong key raises an error
- Tampered ciphertext raises an error
- Key derivation is deterministic and produces 32-byte keys

### API Client (`test_api_client.py`)

- Successful POST returns `True`
- Payload is encrypted (nonce + ciphertext, no plaintext fields in the body)
- HTTP 400 and 500 return `False` without crashing
- Connection errors return `False` without crashing
- `X-API-Key` and `X-Facility-ID` headers are set correctly

---

## Fixture Data

Realistic JSON responses live in `tests/fixtures/braiins/`. These mirror the Braiins OS REST API v1.2.0:

| File | Endpoint | Contents |
|------|----------|----------|
| `auth_login.json` | `POST /api/v1/auth/login` | JWT token + timeout |
| `cooling_state.json` | `GET /api/v1/cooling/state` | 4 fans + highest temp |
| `miner_stats.json` | `GET /api/v1/miner/stats` | Hashrate, pool, power stats |
| `miner_details.json` | `GET /api/v1/miner/details` | UID, serial, hostname, uptime, BOS version |
| `hashboards.json` | `GET /api/v1/miner/hw/hashboards` | 3 boards with temps and chip counts |
| `miner_errors.json` | `GET /api/v1/miner/errors` | 2 errors (temp warning + fan RPM low) |

To update fixture data: edit the JSON files directly. The test suite loads them at runtime, so changes take effect immediately.

---

## CI Integration

Tests run automatically in two places:

1. **`braiins-test.yml`** -- dedicated test workflow, runs on every PR and push to main, tests against Python 3.11
2. **Build workflows** (`build-linux.yml`, `build-macos.yml`, `build-windows.yml`) -- tests run before each build, so a failing test blocks the release
