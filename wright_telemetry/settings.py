"""Runtime settings — thin layer over environment variables.

Override any value by setting the corresponding env var before launching
the agent.  In development, the easiest approach is a shell export or a
``.env`` file sourced by your shell:

    export WRIGHT_API_URL=http://localhost:3001 
    export WRIGHT_INGEST_URL=http://localhost:8080
    export WRIGHT_WS_URL=http://localhost:8082

In production the defaults below are used unchanged.

Env vars
--------
WRIGHT_API_URL
    Base URL for the Wright One API.
    Default: https://api.wrightfan.com/api
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Portal / provisioning API  (access-key redeem, agent-info, agent-config)
# ---------------------------------------------------------------------------
API_URL: str = os.environ.get("WRIGHT_API_URL", "https://api.wrightfan.com/api").rstrip("/")

# ---------------------------------------------------------------------------
# Ingest gateway  (POST /api/v2/telemetry)
# ---------------------------------------------------------------------------
INGEST_URL: str = os.environ.get("WRIGHT_INGEST_URL", "https://api.wrightfan.com/api").rstrip("/")

# ---------------------------------------------------------------------------
# WebSocket gateway  (ws[s]://.../api/v2/ws/agent)
# ---------------------------------------------------------------------------
WS_URL: str = os.environ.get("WRIGHT_WS_URL", "https://api.wrightfan.com/api").rstrip("/")
