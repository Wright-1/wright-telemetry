# Fake Miners — Local Testing Without Real Hardware

Run fake Braiins, Vnish, and LuxOS miners locally using Docker so that
`wright-telemetry` can be exercised end-to-end without any real hardware.
Each fake runs as its own container on an isolated bridge network
(`172.28.0.0/24`), which means `wright-telemetry` can scan a whole subnet
just as it would in production.

### Prerequisites

1. Docker Desktop (or Docker Engine on Linux)
2. **macOS only:** install and run
   [docker-mac-net-connect](https://github.com/chipmk/docker-mac-net-connect)
   so that the `172.28.0.x` addresses are reachable from the host:
   ```bash
   curl -fsSL https://github.com/chipmk/docker-mac-net-connect/releases/download/v0.1.7/docker-mac-net-connect_darwin_arm64.tar.gz \
     | tar -xz -C /tmp
   sudo mv /tmp/docker-mac-net-connect /usr/local/bin/
   sudo chmod +x /usr/local/bin/docker-mac-net-connect
   sudo docker-mac-net-connect   # run once; re-run after reboot
   ```
   On Linux the bridge network is reachable from the host without any extra tools.

### IP layout

| Firmware | IPs              | Port |
|----------|------------------|------|
| Braiins  | `172.28.0.10–19` | 80   |
| Vnish    | `172.28.0.20–29` | 80   |
| LuxOS    | `172.28.0.30–39` | 4028 |

### Starting the fleet

```bash
docker compose -f fake_miners/docker-compose.yml up -d --build
```

### Pointing wright-telemetry at the fake fleet

Run setup and enter the fake subnet when prompted:

```bash
wright-telemetry --setup
# subnet: 172.28.0.0/24
```

### Stopping

```bash
docker compose -f fake_miners/docker-compose.yml down
```

### Editing fixtures

`tests/fixtures/` is mounted read-only into every container. Edit a JSON file
and restart the relevant container — no rebuild needed:

```bash
docker compose -f fake_miners/docker-compose.yml restart braiins-0
```

### Adding more miners

Duplicate any service block in `docker-compose.yml`, bump the last octet of
the IP and the `MINER_INDEX` environment variable, then re-run:

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
- **Auth enforcement** — fakes issue a token on the first login/unlock request
  and require it on subsequent calls, exercising the collector's re-auth path.
  Before any login has occurred all traffic is allowed, so fakes work without
  credentials too.

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
