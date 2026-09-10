# Developer worktree environment (telemetry)

How the agent targets a local stack, and the right one when several feature
branches are running side by side. The cross-repo tooling and the full picture
live in `wright-one-portal/docs/WORKTREE_DEV.md`; this covers the agent's half.

---

## Run it with `scripts/dev.sh`

```bash
./scripts/dev.sh            # Qt GUI (the full app)
./scripts/dev.sh --tui      # Rich TUI path instead
```

It sources `.env` if present, derives the local endpoints from `PORT_OFFSET`,
uses this checkout's `venv`, and prints the three URLs it resolved before
launching.

**Why not just `python main.py --gui`.** `wright_telemetry/settings.py` reads
`WRIGHT_API_URL`, `WRIGHT_INGEST_URL` and `WRIGHT_WS_URL` from the environment
and nothing else — and their defaults are the **production**
`api.wrightfan.com` URLs. Launching by hand therefore points at production, or
at whatever another checkout's shell happened to export. The failure mode is
`Connection refused` against a port nothing is serving:

```
portal_client: access-key redeem exception:
  HTTPConnectionPool(host='localhost', port=3001): ... Connection refused
```

…which is what you get when the agent aims at the main checkout's 3001 while
only a worktree is running.

| | main checkout | offset 100 |
|---|---|---|
| `WRIGHT_API_URL` (portal BFF) | 3001 | 3101 |
| `WRIGHT_INGEST_URL` (ingest-gateway) | 8080 | 8180 |
| `WRIGHT_WS_URL` (ws-gateway) | 8082 | 8182 |

Explicit `WRIGHT_*` values always win over the offset, so a worktree's `.env`
(written by `new-feature.sh`) is authoritative. `.env.example` documents all
three.

Note `WRIGHT_API_URL` is the portal's **Fastify** API (`apps/api`), not the
Next.js web app — the Next proxy strips `Origin` and Better Auth rejects
every POST.

---

## Credentials default to one global file — but you can split them

The API key and facility ID come from redeeming an access key against the
portal. *Entering* one is interactive only (the TUI prompt or the GUI's Access
Key page), but *where it is stored* is configurable. `config.py` resolves in
this order:

1. `$WRIGHT_CONFIG`
2. `config.json` beside a frozen executable
3. `~/.wright-telemetry/.config_path` (pointer file)
4. `~/.wright-telemetry/config.json` (default)

By default, then, every checkout shares one credential file — and a key
redeemed against one worktree's portal database is **not** valid against
another's, so switching worktrees means redeeming again and clobbering the
previous key.

Point `WRIGHT_CONFIG` at a per-worktree file to keep them separate:

```bash
export WRIGHT_CONFIG="$PWD/.wright-config.json"
./scripts/dev.sh
```

(Add it to this worktree's `.env` to make it stick. `.wright-config.json` is
not currently gitignored — put it outside the repo, or add the pattern first.)

---

## Logs

The agent already writes `~/.wright-telemetry/collector.log` via a
`RotatingFileHandler`, so there is nothing to tee and `scripts/dev.sh` adds no
capture of its own.

That path is **global, not per-worktree**: every checkout's run appends to the
same file, so entries interleave if you run the agent from two worktrees.

The portal's `tools/worktree/devlogs.sh` reads it:

```bash
./devlogs.sh telemetry
./devlogs.sh telemetry -g "refused"
```

---

## Prerequisites

- The portal and the pipeline must be running — the agent is outbound-only and
  has no port of its own.
- A `venv` in this checkout. `new-feature.sh` creates one and installs
  `requirements.txt` (which includes PyQt6, so the GUI works); otherwise:
  `python3 -m venv venv && venv/bin/pip install -r requirements.txt`.
- For testing without hardware, the fake miners: `docker compose -f
  fake_miners/docker-compose.yml up -d --build`. On macOS run
  `sudo docker-mac-net-connect` after every reboot or the `172.28.x.y`
  addresses are unreachable.
