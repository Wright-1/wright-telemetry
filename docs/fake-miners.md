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

To trigger a Wright Fan detection scenario, simulate power getting cut to a
single fan — RPM drops to 0 for `duration_s` seconds, then ramps back up to
speed. This is a real switch flip: exactly one fan on the miner drops while
the others hold steady, which is what the detection logic (`_detect_fan_dips`
in `wright_telemetry/scheduler.py`) requires — a dip only counts if it's
isolated to a single fan; more than one fan low at once is treated as
ambiguous (power loss, hardware fault) and ignored.

The easiest way to drive this is the `simulate_fan_dip.py` helper:
```bash
python fake_miners/simulate_fan_dip.py braiins-a --fan 2 --duration 8
```
See `python fake_miners/simulate_fan_dip.py --help` for random-fan/random-miner
and looping modes, plus an `--all-fans` mode for testing that a whole-unit
drop is correctly ignored.

### Per-miner control

The same API is available on each individual miner's `/control` path. Add
`"fan_position"` to target one fan; omit it to dip every fan on the miner at
once (whole-unit power loss — the real detection logic will ignore this):
```bash
# Status for one miner (per-fan breakdown)
curl http://172.28.0.10/control

# Dip one fan on that miner
curl -X POST http://172.28.0.10/control \
  -H 'Content-Type: application/json' \
  -d '{"action": "fan_dip", "duration_s": 8, "fan_position": 2}'

# Restore that fan early
curl -X POST http://172.28.0.10/control \
  -H 'Content-Type: application/json' \
  -d '{"action": "fan_restore", "fan_position": 2}'
```
