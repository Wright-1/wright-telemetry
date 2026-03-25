"""Data models for telemetry metrics."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class MinerIdentity:
    uid: str
    serial_number: str
    hostname: str
    mac_address: str
    wright_fans: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TelemetryPayload:
    """Envelope sent to the Wright Fan API for every metric reading."""

    metric_type: str
    facility_id: str
    miner_identity: MinerIdentity
    data: dict[str, Any]
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_type": self.metric_type,
            "timestamp": self.timestamp,
            "facility_id": self.facility_id,
            "miner_identity": self.miner_identity.to_dict(),
            "data": self.data,
        }


# ---------------------------------------------------------------------------
# Per-metric data containers (typed wrappers around raw Braiins responses)
# ---------------------------------------------------------------------------


@dataclass
class FanReading:
    position: int
    rpm: int
    target_speed_ratio: float


@dataclass
class CoolingData:
    fans: list[FanReading]
    highest_temperature: Optional[dict[str, Any]] = None

    @classmethod
    def from_braiins(cls, raw: dict[str, Any]) -> CoolingData:
        fans = [
            FanReading(
                position=f.get("position", 0),
                rpm=f.get("rpm", 0),
                target_speed_ratio=f.get("target_speed_ratio", 0.0),
            )
            for f in raw.get("fans", [])
        ]
        return cls(fans=fans, highest_temperature=raw.get("highest_temperature"))


@dataclass
class HashrateData:
    miner_stats: dict[str, Any]
    pool_stats: dict[str, Any]
    power_stats: dict[str, Any]

    @classmethod
    def from_braiins(cls, raw: dict[str, Any]) -> HashrateData:
        return cls(
            miner_stats=raw.get("miner_stats", {}),
            pool_stats=raw.get("pool_stats", {}),
            power_stats=raw.get("power_stats", {}),
        )


@dataclass
class UptimeData:
    bosminer_uptime_s: int
    system_uptime_s: int
    hostname: str
    bos_version: dict[str, Any]
    platform: int
    status: int

    @classmethod
    def from_braiins(cls, raw: dict[str, Any]) -> UptimeData:
        return cls(
            bosminer_uptime_s=raw.get("bosminer_uptime_s", 0),
            system_uptime_s=raw.get("system_uptime_s", 0),
            hostname=raw.get("hostname", ""),
            bos_version=raw.get("bos_version", {}),
            platform=raw.get("platform", 0),
            status=raw.get("status", 0),
        )


@dataclass
class HashboardReading:
    board_name: str
    board_temp: Optional[dict[str, Any]]
    highest_chip_temp: Optional[dict[str, Any]]
    lowest_inlet_temp: Optional[dict[str, Any]]
    highest_outlet_temp: Optional[dict[str, Any]]
    chips_count: int
    id: str
    enabled: bool
    stats: dict[str, Any]


@dataclass
class HashboardData:
    hashboards: list[HashboardReading]

    @classmethod
    def from_braiins(cls, raw: dict[str, Any]) -> HashboardData:
        boards = [
            HashboardReading(
                board_name=b.get("board_name", ""),
                board_temp=b.get("board_temp"),
                highest_chip_temp=b.get("highest_chip_temp"),
                lowest_inlet_temp=b.get("lowest_inlet_temp"),
                highest_outlet_temp=b.get("highest_outlet_temp"),
                chips_count=b.get("chips_count", 0),
                id=b.get("id", ""),
                enabled=b.get("enabled", False),
                stats=b.get("stats", {}),
            )
            for b in raw.get("hashboards", [])
        ]
        return cls(hashboards=boards)


@dataclass
class ErrorEntry:
    message: str
    timestamp: str
    error_codes: list[dict[str, Any]]
    components: list[dict[str, Any]]


@dataclass
class ErrorData:
    errors: list[ErrorEntry]

    @classmethod
    def from_braiins(cls, raw: dict[str, Any]) -> ErrorData:
        entries = [
            ErrorEntry(
                message=e.get("message", ""),
                timestamp=e.get("timestamp", ""),
                error_codes=e.get("error_codes", []),
                components=e.get("components", []),
            )
            for e in raw.get("errors", [])
        ]
        return cls(errors=entries)
