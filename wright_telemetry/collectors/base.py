"""Abstract base class that every miner collector must implement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from wright_telemetry.models import (
    CoolingData,
    ErrorData,
    HashboardData,
    HashrateData,
    MinerIdentity,
    UptimeData,
)


class MinerCollector(ABC):
    """Interface for collecting telemetry from a single miner.

    Each concrete adapter (Braiins, Vnish, LuxOS, ...) subclasses this and
    implements the fetch methods that hit the miner's local API.
    """

    def __init__(self, url: str, username: Optional[str] = None, password: Optional[str] = None):
        self.url = url.rstrip("/")
        self.username = username
        self.password = password

    @abstractmethod
    def authenticate(self) -> None:
        """Establish a session / token with the miner.  No-op if auth is not required."""

    @abstractmethod
    def fetch_identity(self) -> MinerIdentity:
        """Return the miner's unique identity (uid, serial, hostname, mac)."""

    @abstractmethod
    def fetch_cooling(self) -> CoolingData:
        """Fetch temperature + fan RPM data."""

    @abstractmethod
    def fetch_hashrate(self) -> HashrateData:
        """Fetch hashrate, pool, and power statistics."""

    @abstractmethod
    def fetch_uptime(self) -> UptimeData:
        """Fetch uptime and firmware details."""

    @abstractmethod
    def fetch_hashboards(self) -> HashboardData:
        """Fetch per-hashboard chip temperatures and status."""

    @abstractmethod
    def fetch_errors(self) -> ErrorData:
        """Fetch miner error log."""

    # Convenience mapping: metric name -> fetch method
    def get_fetcher(self, metric: str) -> Any:
        mapping = {
            "cooling": self.fetch_cooling,
            "hashrate": self.fetch_hashrate,
            "uptime": self.fetch_uptime,
            "hashboards": self.fetch_hashboards,
            "errors": self.fetch_errors,
        }
        return mapping.get(metric)
