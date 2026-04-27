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
    wright_fans: Optional[bool] = None
    ip_address: str = ""
    firmware: Optional[str] = None

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
# Per-metric data containers (firmware-agnostic, with from_* factory methods)
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

    @classmethod
    def from_luxos(cls, fans_raw: dict[str, Any], temps_raw: dict[str, Any]) -> CoolingData:
        fans = [
            FanReading(
                position=f.get("ID", 0),
                rpm=f.get("RPM", 0),
                target_speed_ratio=f.get("Speed", 0) / 100.0,
            )
            for f in fans_raw.get("FANS", [])
        ]
        highest_temp: Optional[dict[str, Any]] = None
        temps_list = temps_raw.get("TEMPS", [])
        if temps_list:
            all_temps: list[float] = []
            for t in temps_list:
                for key in ("Board", "Chip", "TopLeft", "TopRight", "BottomLeft", "BottomRight"):
                    val = t.get(key)
                    if isinstance(val, (int, float)) and val > 0:
                        all_temps.append(float(val))
            if all_temps:
                highest_temp = {"value": max(all_temps), "unit": "C"}
        return cls(fans=fans, highest_temperature=highest_temp)

    @classmethod
    def from_vnish(cls, raw: dict[str, Any]) -> CoolingData:
        fans = [
            FanReading(
                position=f.get("id", 0),
                rpm=f.get("rpm", 0),
                target_speed_ratio=f.get("speed_pct", 0) / 100.0,
            )
            for f in raw.get("fans", [])
        ]
        highest_temp: Optional[dict[str, Any]] = None
        chains = raw.get("chains", [])
        if chains:
            all_temps: list[float] = []
            for c in chains:
                for key in ("temp_board", "temp_chip"):
                    val = c.get(key)
                    if isinstance(val, (int, float)) and val > 0:
                        all_temps.append(float(val))
            if all_temps:
                highest_temp = {"value": max(all_temps), "unit": "C"}
        return cls(fans=fans, highest_temperature=highest_temp)


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

    @classmethod
    def from_luxos(
        cls,
        summary_raw: dict[str, Any],
        pools_raw: dict[str, Any],
        power_raw: dict[str, Any],
    ) -> HashrateData:
        summary = (summary_raw.get("SUMMARY") or [{}])[0]
        miner_stats = {
            "ghs_5s": summary.get("GHS 5s", 0),
            "ghs_30m": summary.get("GHS 30m", 0),
            "ghs_av": summary.get("GHS av", 0),
            "total_mh": summary.get("Total MH", 0),
            "hardware_errors": summary.get("Hardware Errors", 0),
            "utility": summary.get("Utility", 0),
            "work_utility": summary.get("Work Utility", 0),
        }
        pools = pools_raw.get("POOLS", [])
        pool_stats = {
            "pools": [
                {
                    "url": p.get("URL", ""),
                    "user": p.get("User", ""),
                    "status": p.get("Status", ""),
                    "accepted": p.get("Accepted", 0),
                    "rejected": p.get("Rejected", 0),
                    "stale": p.get("Stale", 0),
                    "difficulty_accepted": p.get("Difficulty Accepted", 0),
                    "pool_rejected_pct": p.get("Pool Rejected%", 0),
                    "pool_stale_pct": p.get("Pool Stale%", 0),
                }
                for p in pools
            ],
        }
        power = (power_raw.get("POWER") or [{}])[0]
        power_stats = {
            "watts": power.get("Watts", 0),
            "psu_reporting": power.get("PSU", False),
        }
        return cls(miner_stats=miner_stats, pool_stats=pool_stats, power_stats=power_stats)

    @classmethod
    def from_vnish(cls, raw: dict[str, Any]) -> HashrateData:
        miner = raw.get("miner", {})
        miner_stats = {
            "ghs_5s": miner.get("instant_hashrate", 0),
            "ghs_av": miner.get("average_hashrate", 0),
            "hardware_errors": miner.get("hardware_errors", 0),
        }
        pools = raw.get("pools", [])
        pool_stats = {
            "pools": [
                {
                    "url": p.get("url", ""),
                    "user": p.get("user", ""),
                    "status": p.get("status", ""),
                    "accepted": p.get("accepted", 0),
                    "rejected": p.get("rejected", 0),
                    "stale": p.get("stale", 0),
                    "difficulty_accepted": p.get("difficulty_accepted", 0),
                    "pool_rejected_pct": p.get("pool_rejected_pct", 0),
                    "pool_stale_pct": p.get("pool_stale_pct", 0),
                }
                for p in pools
            ],
        }
        power = raw.get("power", {})
        power_stats = {
            "watts": power.get("watts", 0),
            "efficiency": power.get("efficiency", 0),
        }
        return cls(miner_stats=miner_stats, pool_stats=pool_stats, power_stats=power_stats)


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

    @classmethod
    def from_luxos(
        cls,
        summary_raw: dict[str, Any],
        version_raw: dict[str, Any],
        config_raw: dict[str, Any],
    ) -> UptimeData:
        summary = (summary_raw.get("SUMMARY") or [{}])[0]
        version = (version_raw.get("VERSION") or [{}])[0]
        config = (config_raw.get("CONFIG") or [{}])[0]
        elapsed = summary.get("Elapsed", 0)
        return cls(
            bosminer_uptime_s=elapsed,
            system_uptime_s=elapsed,
            hostname=config.get("Hostname", ""),
            bos_version={
                "luxminer": version.get("LUXminer", ""),
                "api": version.get("API", ""),
                "type": version.get("Type", ""),
            },
            platform=0,
            status=0,
        )

    @classmethod
    def from_vnish(cls, info_raw: dict[str, Any], summary_raw: dict[str, Any]) -> UptimeData:
        miner = summary_raw.get("miner", {})
        elapsed = miner.get("uptime", 0)
        return cls(
            bosminer_uptime_s=elapsed,
            system_uptime_s=elapsed,
            hostname=info_raw.get("hostname", ""),
            bos_version={
                "vnish": info_raw.get("firmware_version", ""),
                "model": info_raw.get("model", ""),
            },
            platform=0,
            status=0,
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

    @classmethod
    def from_luxos(cls, devs_raw: dict[str, Any], temps_raw: dict[str, Any]) -> HashboardData:
        temps_by_id: dict[int, dict[str, Any]] = {}
        for t in temps_raw.get("TEMPS", []):
            temps_by_id[t.get("ID", t.get("TEMP", -1))] = t

        boards: list[HashboardReading] = []
        for dev in devs_raw.get("DEVS", []):
            board_id = dev.get("ASC", dev.get("ID", 0))
            temp_info = temps_by_id.get(board_id, {})
            board_temp_val = dev.get("Temperature")
            board_temp = {"value": board_temp_val, "unit": "C"} if board_temp_val else temp_info.get("Board")
            chip_temps = [
                temp_info.get(k)
                for k in ("Chip", "TopLeft", "TopRight", "BottomLeft", "BottomRight")
                if temp_info.get(k) is not None
            ]
            highest_chip = {"value": max(chip_temps), "unit": "C"} if chip_temps else None
            inlet_temps = [temp_info[k] for k in ("TopLeft",) if k in temp_info]
            lowest_inlet = {"value": min(inlet_temps), "unit": "C"} if inlet_temps else None
            outlet_temps = [temp_info[k] for k in ("BottomLeft",) if k in temp_info]
            highest_outlet = {"value": max(outlet_temps), "unit": "C"} if outlet_temps else None

            boards.append(HashboardReading(
                board_name=dev.get("Board", dev.get("Connector", f"ASC {board_id}")),
                board_temp=board_temp,
                highest_chip_temp=highest_chip,
                lowest_inlet_temp=lowest_inlet,
                highest_outlet_temp=highest_outlet,
                chips_count=0,
                id=str(board_id),
                enabled=dev.get("Enabled", "N") == "Y",
                stats={
                    "mhs_av": dev.get("MHS av", 0),
                    "mhs_5s": dev.get("MHS 5s", 0),
                    "mhs_15m": dev.get("MHS 15m", 0),
                    "accepted": dev.get("Accepted", 0),
                    "rejected": dev.get("Rejected", 0),
                    "hardware_errors": dev.get("Hardware Errors", 0),
                    "status": dev.get("Status", ""),
                    "serial_number": dev.get("SerialNumber", ""),
                    "nominal_mhs": dev.get("Nominal MHS", 0),
                    "profile": dev.get("Profile", ""),
                },
            ))
        return cls(hashboards=boards)

    @classmethod
    def from_vnish(cls, raw: dict[str, Any]) -> HashboardData:
        boards: list[HashboardReading] = []
        for chain in raw.get("chains", []):
            board_id = chain.get("id", 0)
            board_temp_val = chain.get("temp_board")
            board_temp = {"value": board_temp_val, "unit": "C"} if board_temp_val is not None else None
            chip_temp_val = chain.get("temp_chip")
            highest_chip = {"value": chip_temp_val, "unit": "C"} if chip_temp_val is not None else None

            boards.append(HashboardReading(
                board_name=chain.get("name", f"Chain {board_id}"),
                board_temp=board_temp,
                highest_chip_temp=highest_chip,
                lowest_inlet_temp=None,
                highest_outlet_temp=None,
                chips_count=chain.get("chips", 0),
                id=str(board_id),
                enabled=chain.get("status", "") == "ok",
                stats={
                    "hashrate": chain.get("hashrate", 0),
                    "accepted": chain.get("accepted", 0),
                    "rejected": chain.get("rejected", 0),
                    "hardware_errors": chain.get("hw_errors", 0),
                    "serial_number": chain.get("serial", ""),
                },
            ))
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

    @classmethod
    def from_luxos(cls, events_raw: dict[str, Any]) -> ErrorData:
        entries = [
            ErrorEntry(
                message=e.get("Description", ""),
                timestamp=e.get("CreatedAt", ""),
                error_codes=[{"code": e.get("Code", ""), "doc_url": e.get("DocUrl", "")}],
                components=[{"target": e.get("Target", ""), "id": e.get("ID", "")}],
            )
            for e in events_raw.get("EVENTS", [])
        ]
        return cls(errors=entries)

    @classmethod
    def from_vnish(cls, raw: dict[str, Any]) -> ErrorData:
        entries = [
            ErrorEntry(
                message=e.get("message", ""),
                timestamp=e.get("timestamp", ""),
                error_codes=[{"code": e.get("code", ""), "severity": e.get("severity", "")}],
                components=[{"type": e.get("component_type", ""), "id": e.get("component_id", "")}],
            )
            for e in raw.get("errors", [])
        ]
        return cls(errors=entries)
