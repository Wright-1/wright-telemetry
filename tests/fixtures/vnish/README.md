# Vnish fixtures

Captured 2026-08-11 from a live **Antminer S21 running Vnish 1.2.6-rc5** via
`scripts/vnish_api_dump.ps1`.

**Anonymised.** MAC, IP, gateway, hostname, pool URLs, pool worker names, the
session token, and the custom build name/UUID are all replaced with
placeholders. Pool hosts use `examplepool.io` and the reserved TEST-NET-3
range (`203.0.113.0/24`). Everything else — hashrates, temps, fan RPM, chip
counts, share counts — is verbatim from the miner, since that is what the
parsers are being tested against. Keep it that way when refreshing these:
re-anonymise before committing a new capture.

| File | Endpoint | Notes |
|---|---|---|
| `unlock.json` | `POST /api/v1/unlock` | token is a placeholder, not a real one |
| `info.json` | `GET /api/v1/info` | identity + firmware. Note `fw_version`, **not** `firmware_version`; hostname/MAC are under `system.network_status` |
| `summary.json` | `GET /api/v1/summary` | the workhorse: hashrate, pools, power, `cooling.fans`, `chains` |
| `status.json` | `GET /api/v1/status` | only miner state flags — **no** fans/chains/errors |
| `summary_faulted.json` | — | **synthetic**, hand-built failure state (stopped miner, dead fan, failed chain) |

Units are mixed and easy to get wrong:

- `instant_hashrate` / `average_hashrate` are **TH/s**
- `hr_realtime` / `hr_average` / `hr_nominal` / `hr_stock` and per-chain
  `hashrate_rt` / `hashrate_ideal` are **GH/s** (matching `info.hr_measure`)

The collector reads the GH/s fields so `ghs_5s` means what it says.

This firmware reports `serial: "N/A"` and the stock hostname `Antminer`, and
serves `/api/v1/info` and `/api/v1/summary` without authentication.
`/boards`, `/pools`, `/factory-info`, `/mining/presets` and `/system/log` all
404 here.
