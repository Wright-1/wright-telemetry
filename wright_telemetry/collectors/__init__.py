"""Miner collector adapters.

Importing this package registers every collector adapter with
``CollectorFactory`` (each module calls ``CollectorFactory.register(...)``
as a decorator at import time). Any entry point that calls
``CollectorFactory.create(...)`` must import ``wright_telemetry.collectors``
first — relying on a call site to import individual adapter modules is
what let the registry stay empty on the ``--subnets-file`` headless path.
"""

from wright_telemetry.collectors import (  # noqa: F401
    bitmain,
    braiins,
    luxos,
    sealminer,
    vnish,
    whatsminer,
)
