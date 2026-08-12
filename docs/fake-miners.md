# Fake Miners — Local Testing Without Real Hardware

Run fake Braiins, Vnish, LuxOS, and Bitmain miners locally using Docker so that
`wright-telemetry` can be exercised end-to-end without any real hardware.
Each fake runs as its own container across three isolated bridge networks
(`172.28.0.0/24`, `172.28.1.0/24`, `172.28.2.0/24`) simulating separate
facility VLANs, which means `wright-telemetry` can scan whole subnets just as
it would in production.

### Prerequisites

1. Docker Desktop (or Docker Engine on Linux)
2. **macOS only:** install and run
   [docker-mac-net-connect](https://github.com/chipmk/docker-mac-net-connect)
   so that the `172.28.0.x` addresses are reachable from the host.

   **First time — install and start:**
   ```bash
   curl -fsSL https://github.com/chipmk/docker-mac-net-connect/releases/download/v0.1.7/docker-mac-net-connect_darwin_arm64.tar.gz \
     | tar -xz -C /tmp
   sudo mv /tmp/docker-mac-net-connect /usr/local/bin/
   sudo chmod +x /usr/local/bin/docker-mac-net-connect
   sudo docker-mac-net-connect
   ```

   **After every reboot — just start it:**
   ```bash
   sudo docker-mac-net-connect
   ```

   This is the required step before scanning the `172.28.0.0/24` subnet from
   the host. Without it macOS silently drops packets to Docker bridge IPs and
   no miners will be discovered.

   On Linux the bridge network is reachable from the host without any extra tools.

### IP layout

Three subnets, each holding one miner of every firmware. Within each `/24` the
last octet follows the same scheme:

| Firmware | Last octet | Port | Subnet A     | Subnet B     | Subnet C     |
|----------|-----------|------|--------------|--------------|--------------|
| Braiins  | `.10`     | 80   | `172.28.0.10` | `172.28.1.10` | `172.28.2.10` |
| Vnish    | `.20`     | 80   | `172.28.0.20` | `172.28.1.20` | `172.28.2.20` |
| LuxOS    | `.30`     | 4028 | `172.28.0.30` | `172.28.1.30` | `172.28.2.30` |
| Bitmain  | `.40`     | 80   | `172.28.0.40` | `172.28.1.40` | `172.28.2.40` |

Service names follow the subnet letter — `braiins-a`, `vnish-b`, `bitmain-c`,
and so on.

### Credentials

**Every firmware has its own password.** Real fleets set different credentials
per firmware, so the fake fleet does too — pointing a collector at the wrong
firmware's password fails with a `401` instead of silently working, which is
what makes the per-firmware credentials feature testable.

| Firmware  | Username | Password       | Enforced as                        |
|-----------|----------|----------------|------------------------------------|
| Braiins   | `root`   | `braiins-pw`   | `POST /api/v1/auth/login`          |
| Vnish     | `root`   | `vnish-pw`     | `POST /api/v1/unlock`              |
| LuxOS     | `root`   | `luxos-pw`     | not checked — see below            |
| Bitmain   | `root`   | `bitmain-pw`   | HTTP Digest Auth (RFC 2617)        |
| Sealminer | `root`   | `sealminer-pw` | not checked — see below            |

LuxOS and Sealminer speak raw cgminer TCP on port 4028, which is
unauthenticated on real hardware too. Their password is configured for
consistency but never validated.

> **Note:** `docker-compose.yml` currently defines Braiins, Vnish, LuxOS, and
> Bitmain containers only. Sealminer is listed above because `server.py` and
> `tests/fixtures/sealminer/` support it — add a service block with
> `FIRMWARE: sealminer` on port 4028 if you need it in the fleet.

These defaults match the per-firmware constants in `tests/conftest.py`, so the
unit tests and the fake fleet agree on which password belongs to which
firmware. Override per container with the `MINER_USERNAME` / `MINER_PASSWORD`
environment variables in `docker-compose.yml`:

```yaml
  braiins-a:
    <<: *miner-base
    environment:
      FIRMWARE: braiins
      MINER_USERNAME: admin
      MINER_PASSWORD: something-else
      MINER_INDEX: "0"
```

### Starting the fleet

```bash
docker compose -f fake_miners/docker-compose.yml up -d --build
```

### Pointing wright-telemetry at the fake fleet

Run setup and enter the fake subnets when prompted. The wizard then asks for a
username and password **once per enabled firmware** — enter the matching
password from the table above:

```bash
wright-telemetry --setup
# subnets: 172.28.0.0/24, 172.28.1.0/24, 172.28.2.0/24
# braiins  → root / braiins-pw
# vnish    → root / vnish-pw
# luxos    → root / luxos-pw
# bitmain  → root / bitmain-pw
```

In the GUI, the same credentials go in the per-firmware rows under **Miner
credentials** on the Discover page. Rows only appear for firmware types that
are toggled on.

Or write them straight into `~/.wright-telemetry/config.json` (passwords are
base64, not encrypted):

```json
"discovery": {
  "enabled": true,
  "subnets": ["172.28.0.0/24", "172.28.1.0/24", "172.28.2.0/24"],
  "firmware_credentials": {
    "braiins": {"username": "root", "password_b64": "YnJhaWlucy1wdw=="},
    "vnish":   {"username": "root", "password_b64": "dm5pc2gtcHc="},
    "luxos":   {"username": "root", "password_b64": "bHV4b3MtcHc="},
    "bitmain": {"username": "root", "password_b64": "Yml0bWFpbi1wdw=="}
  }
}
```

Any firmware without an entry falls back to the legacy global
`default_username` / `default_password_b64`, so older configs keep working.

**If miners are discovered but report no telemetry**, the credentials are
almost certainly mismatched — check the container log for a rejected login:

```bash
docker compose -f fake_miners/docker-compose.yml logs braiins-a | grep -i "rejected login"
```

### Stopping

```bash
docker compose -f fake_miners/docker-compose.yml down
```

### Editing fixtures

`tests/fixtures/` is mounted read-only into every container. Edit a JSON file
and restart the relevant container — no rebuild needed:

```bash
docker compose -f fake_miners/docker-compose.yml restart braiins-a
```

### Adding more miners

Duplicate any service block in `docker-compose.yml`, bump the last octet of
the IP and the `MINER_INDEX` environment variable, keep or override
`MINER_PASSWORD`, then re-run:

```bash
docker compose -f fake_miners/docker-compose.yml up -d --build
```

---

## What the fakes simulate

- **Unique identity** — each fake gets its own hostname, MAC address, serial
  number, and UID derived from its index, so every miner shows up as a
  distinct device in the dashboard.
- **Jittered hashrate** — hashrate values are slightly randomised per index
  (deterministic: same index → same numbers on every restart).
- **Realistic fan oscillation** — fan RPMs follow a slow sinusoidal curve
  (±4%, 60-second period, phase-shifted per miner) so fan-detection logic
  sees real movement rather than a frozen number.
- **Per-firmware credentials** — each firmware validates its own password (see
  [Credentials](#credentials)). A wrong password gets a `401`, so a
  misconfigured collector fails loudly rather than quietly collecting.
- **Auth enforcement** — fakes issue a token on a successful login/unlock and
  require it on subsequent calls, exercising the collector's re-auth path.
  Three states, mirroring real hardware:
  - *No login attempted yet* → all traffic allowed, so collectors configured
    with no credentials still work (as against a miner with no password set).
  - *Login succeeded* → every later request must carry the issued token.
  - *Login rejected* → the miner locks and serves nothing anonymously.
    Without this a collector holding the wrong password would still be served
    on a fresh container, and the per-firmware passwords would only take
    effect once some other collector happened to log in correctly first.

---

## Fan dip simulation (Wright Fan detection testing)

To trigger a Wright Fan detection scenario, drop all fans to 0 RPM on demand:

### Per-miner control

The same API is available on each individual miner's `/control` path:
```bash
# Status for one miner
curl http://172.28.0.10/control

# Dip just that miner
curl -X POST http://172.28.0.10/control \
  -H 'Content-Type: application/json' \
  -d '{"action": "fan_dip", "duration_s": 8}'
```
