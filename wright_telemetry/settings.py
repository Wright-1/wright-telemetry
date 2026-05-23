"""Runtime settings — thin layer over environment variables.

Override any value by setting the corresponding env var before launching
the agent.  In development, the easiest approach is a shell export or a
``.env`` file sourced by your shell:

    export WRIGHT_API_URL=http://localhost:3001

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
# API base URL
# ---------------------------------------------------------------------------
# The provisioning page POSTs to  {API_URL}/internal/provision/redeem
# The telemetry agent connects to {API_URL}/v2/...
#
# Override in dev:   export WRIGHT_API_URL=http://localhost:3001
API_URL: str = os.environ.get("WRIGHT_API_URL", "https://api.wrightfan.com/api").rstrip("/")
