"""Data models for telemetry metrics."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


def _as_float(value: Any) -> float:
    """Coerce a possibly-stringy numeric API field to float, 0.0 on failure.

    WhatsMiner's ``get_psu`` returns its numbers as JSON strings ("13968"),
    while every other btminer command returns real numbers.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class MinerIdentity:
    uid: str
    serial_number: str
    hostname: str
    mac_address: str
    model: str = ""
    wright_fans: Optional[bool] = None
    ip_address: str = ""
    firmware: Optional[str] = None
    nominal_hashrate_ghs: Optional[float] = None

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

    @classmethod
    def from_bitmain(cls, raw: dict[str, Any]) -> CoolingData:
        stats = (raw.get("STATS") or [{}])[0]
        # Fan RPMs are a flat integer array — synthesize FanReading objects.
        fans = [
            FanReading(position=i, rpm=rpm, target_speed_ratio=0.0)
            for i, rpm in enumerate(stats.get("fan", []))
        ]
        # Highest temp (board or chip) across all chains.
        all_temps: list[float] = []
        for chain in stats.get("chain", []):
            for key in ("temp_pcb", "temp_chip"):
                for t in chain.get(key, []):
                    if isinstance(t, (int, float)) and t > 0:
                        all_temps.append(float(t))
        highest_temp: Optional[dict[str, Any]] = (
            {"value": max(all_temps), "unit": "C"} if all_temps else None
        )
        return cls(fans=fans, highest_temperature=highest_temp)

    @classmethod
    def from_sealminer(cls, stats_raw: dict[str, Any]) -> CoolingData:
        stats = (stats_raw.get("STATS") or [{}])[0]
        fan_count = int(stats.get("Fan Count", 0))
        fans = [
            FanReading(
                position=i,
                rpm=int(stats.get(f"{i} Speed", 0)),
                target_speed_ratio=round(int(stats.get(f"{i} PWM", 0)) / 255.0, 4),
            )
            for i in range(fan_count)
        ]
        all_temps: list[float] = []
        board_count = int(stats.get("Board Count", 0))
        for i in range(board_count):
            sensor_temps = [
                float(stats[f"{i} Temp {j}"])
                for j in range(4)
                if isinstance(stats.get(f"{i} Temp {j}"), (int, float)) and stats[f"{i} Temp {j}"] > 0
            ]
            if sensor_temps:
                all_temps.append(max(sensor_temps))
        psu_amb = stats.get("PSU Temp AMB")
        if isinstance(psu_amb, (int, float)) and psu_amb > 0:
            all_temps.append(float(psu_amb))
        highest_temp: Optional[dict[str, Any]] = (
            {"value": max(all_temps), "unit": "C"} if all_temps else None
        )
        return cls(fans=fans, highest_temperature=highest_temp)

    @classmethod
    def from_whatsminer(cls, summary_raw: dict[str, Any]) -> CoolingData:
        summary = (summary_raw.get("SUMMARY") or [{}])[0]
        # btminer reports one intake and one exhaust speed rather than a
        # per-fan array, so position 0 is air-in and 1 is air-out. It exposes
        # no fan PWM/duty anywhere in the readable API, so target_speed_ratio
        # stays 0.0 (same limitation as the Sealminer/Bitmain adapters).
        fans: list[FanReading] = []
        for position, key in enumerate(("Fan Speed In", "Fan Speed Out")):
            rpm = summary.get(key)
            if rpm is None:
                continue
            fans.append(FanReading(position=position, rpm=int(rpm), target_speed_ratio=0.0))
        # "Env Temp" is ambient intake air, not a component temperature, so it
        # is deliberately excluded from the highest-temperature calculation.
        all_temps = [
            float(summary[key])
            for key in ("Chip Temp Max", "Temperature")
            if isinstance(summary.get(key), (int, float)) and summary[key] > 0
        ]
        highest_temp: Optional[dict[str, Any]] = (
            {"value": max(all_temps), "unit": "C"} if all_temps else None
        )
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
        devs_raw: dict[str, Any] | None = None,
    ) -> HashrateData:
        summary = (summary_raw.get("SUMMARY") or [{}])[0]
        devs = (devs_raw or {}).get("DEVS") or []
        miner_stats = {
            "ghs_5s": summary.get("GHS 5s", 0),
            "ghs_30m": summary.get("GHS 30m", 0),
            "ghs_av": summary.get("GHS av", 0),
            "total_mh": summary.get("Total MH", 0),
            "hardware_errors": summary.get("Hardware Errors", 0),
            "utility": summary.get("Utility", 0),
            "work_utility": summary.get("Work Utility", 0),
            "nominal_ghs": sum(d.get("Nominal MHS", 0) for d in devs) / 1000,
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
            "hr_nominal": miner.get("hr_nominal", 0),
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

    @classmethod
    def from_bitmain(
        cls,
        stats_raw: dict[str, Any],
        pools_raw: dict[str, Any],
    ) -> HashrateData:
        stats = (stats_raw.get("STATS") or [{}])[0]
        miner_stats = {
            "ghs_5s": stats.get("rate_5s", 0),
            "ghs_30m": stats.get("rate_30m", 0),
            "ghs_av": stats.get("rate_avg", 0),
            "rate_ideal": stats.get("rate_ideal", 0),
            "rate_unit": stats.get("rate_unit", "GH/s"),
        }
        pools = pools_raw.get("POOLS", [])
        pool_stats = {
            "pools": [
                {
                    "url": p.get("url", ""),
                    "user": p.get("user", ""),
                    "status": p.get("status", ""),
                    "accepted": p.get("accepted", 0),
                    "rejected": p.get("rejected", 0),
                    "stale": p.get("stale", 0),
                    "difficulty_accepted": p.get("diffa", 0),
                    "pool_rejected_pct": 0,
                    "pool_stale_pct": 0,
                }
                for p in pools
            ],
        }
        power_stats = {
            "watts": stats.get("watt", 0),
            "efficiency": stats.get("jt", 0),
        }
        return cls(miner_stats=miner_stats, pool_stats=pool_stats, power_stats=power_stats)

    @classmethod
    def from_sealminer(
        cls,
        summary_raw: dict[str, Any],
        pools_raw: dict[str, Any],
        stats_raw: dict[str, Any],
    ) -> HashrateData:
        summary = (summary_raw.get("SUMMARY") or [{}])[0]
        stats = (stats_raw.get("STATS") or [{}])[0]
        # bdminer's "MHS av" is a since-boot lifetime average: it stays high
        # after a miner goes idle/suspended, and the pipeline maps ghs_av to the
        # actual-hashrate ("1h") metric that drives billing — so a stopped miner
        # would over-report. Use the best recent rolling window instead
        # (15m -> 5m -> 1m); a present-but-zero window is honest (miner idle).
        # Fall back to the lifetime average only if no rolling window is reported.
        rolling_avg_mhs: Optional[float] = next(
            (summary[w] for w in ("MHS 15m", "MHS 5m", "MHS 1m") if summary.get(w) is not None),
            summary.get("MHS av"),
        )
        miner_stats = {
            "ghs_5s": summary.get("MHS 5s", 0) / 1000,
            "ghs_1m": summary.get("MHS 1m", 0) / 1000,
            "ghs_5m": summary.get("MHS 5m", 0) / 1000,
            "ghs_15m": summary.get("MHS 15m", 0) / 1000,
            "ghs_av": (rolling_avg_mhs or 0) / 1000,
            "hardware_errors": summary.get("Hardware Errors", 0),
            "nominal_ghs": stats.get("MHS(Ideal)", 0) / 1000,
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
        power_stats = {
            "watts": stats.get("PSU Input Power", 0),
            # bdminer reports true mining efficiency as W/T = watts-per-terahash
            # (J/TH), matching what the pipeline stores as efficiency_j_per_th.
            # "PSU Efficiency" is a 0-1 electrical ratio — a different metric —
            # so it is exposed separately rather than as `efficiency`.
            "efficiency": stats.get("W/T(Avg)", 0),
            "psu_efficiency": stats.get("PSU Efficiency", 0),
        }
        return cls(miner_stats=miner_stats, pool_stats=pool_stats, power_stats=power_stats)

    @classmethod
    def from_whatsminer(
        cls,
        summary_raw: dict[str, Any],
        pools_raw: dict[str, Any],
        psu_raw: dict[str, Any],
    ) -> HashrateData:
        summary = (summary_raw.get("SUMMARY") or [{}])[0]
        psu = psu_raw.get("Msg") or {}
        # btminer's "MHS av" is a since-boot lifetime average, so it stays high
        # after a miner goes idle and the pipeline maps ghs_av to the billing
        # ("1h") metric. Prefer the best recent rolling window, exactly as the
        # Sealminer adapter does; fall back to the lifetime average only when no
        # window is reported.
        rolling_avg_mhs: Optional[float] = next(
            (summary[w] for w in ("MHS 15m", "MHS 5m", "MHS 1m") if summary.get(w) is not None),
            summary.get("MHS av"),
        )
        miner_stats = {
            "ghs_5s": summary.get("MHS 5s", 0) / 1000,
            "ghs_1m": summary.get("MHS 1m", 0) / 1000,
            "ghs_5m": summary.get("MHS 5m", 0) / 1000,
            "ghs_15m": summary.get("MHS 15m", 0) / 1000,
            "ghs_av": (rolling_avg_mhs or 0) / 1000,
            "total_mh": summary.get("Total MH", 0),
            "hardware_errors": summary.get("Hardware Errors", 0),
            # "Factory GHS" is already in GH/s (unlike every "MHS *" field),
            # so it is used as-is with no /1000 conversion.
            "nominal_ghs": summary.get("Factory GHS", 0),
            "hash_deviation_pct": summary.get("Hash Deviation%", 0),
            "hash_stable": summary.get("Hash Stable", False),
            "freq_avg": summary.get("freq_avg", 0),
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
        power_stats = {
            "watts": summary.get("Power", 0),
            # "Power Rate" is W/TH == J/TH, matching efficiency_j_per_th.
            "efficiency": summary.get("Power Rate", 0),
            "power_limit": summary.get("Power Limit", 0),
            "power_mode": summary.get("Power Mode", ""),
            # get_psu reports current in 1mA units and voltage in 10mV units.
            "psu_amps": _as_float(psu.get("iin")) / 1000,
            "psu_volts": _as_float(psu.get("vin")) / 100,
            "psu_fan_rpm": int(_as_float(psu.get("fan_speed"))),
        }
        return cls(miner_stats=miner_stats, pool_stats=pool_stats, power_stats=power_stats)

    def get_nominal_ghs(self) -> Optional[float]:
        ms = self.miner_stats
        if "rate_ideal" in ms:
            return float(ms["rate_ideal"])
        if "nominal_hashrate" in ms:
            return float((ms["nominal_hashrate"] or {}).get("gigahash_per_second", 0))
        if "nominal_ghs" in ms:
            v = ms["nominal_ghs"]
            return float(v) if v else None
        if "hr_nominal" in ms:
            return float(ms["hr_nominal"])
        return None


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

    @classmethod
    def from_bitmain(
        cls,
        stats_raw: dict[str, Any],
        sysinfo_raw: dict[str, Any],
    ) -> UptimeData:
        stats = (stats_raw.get("STATS") or [{}])[0]
        elapsed = stats.get("elapsed", 0)
        return cls(
            bosminer_uptime_s=elapsed,
            system_uptime_s=elapsed,
            hostname=sysinfo_raw.get("hostname", ""),
            bos_version={
                "firmware": sysinfo_raw.get("system_filesystem_version", ""),
                "firmware_type": sysinfo_raw.get("firmware_type", ""),
            },
            platform=0,
            status=0,
        )

    @classmethod
    def from_sealminer(
        cls,
        summary_raw: dict[str, Any],
        stats_raw: dict[str, Any],
    ) -> UptimeData:
        summary = (summary_raw.get("SUMMARY") or [{}])[0]
        stats = (stats_raw.get("STATS") or [{}])[0]
        miner_elapsed = int(summary.get("Elapsed", 0))
        system_elapsed = int(stats.get("System Uptime", miner_elapsed))
        return cls(
            bosminer_uptime_s=miner_elapsed,
            system_uptime_s=system_elapsed,
            hostname="",
            bos_version={
                "firmware": stats.get("Firmware", ""),
                "software_version": stats.get("Software Version", ""),
                "mining_mode": stats.get("Mining Mode", ""),
                "pm_state": stats.get("PM State", ""),
            },
            platform=0,
            status=0,
        )

    @classmethod
    def from_whatsminer(
        cls,
        summary_raw: dict[str, Any],
        version_raw: dict[str, Any],
        info_raw: dict[str, Any],
    ) -> UptimeData:
        summary = (summary_raw.get("SUMMARY") or [{}])[0]
        version = version_raw.get("Msg") or {}
        info = info_raw.get("Msg") or {}
        # btminer distinguishes the two: "Elapsed" is how long the mining
        # process has been hashing, "Uptime" is control-board uptime.
        miner_elapsed = int(summary.get("Elapsed", 0))
        return cls(
            bosminer_uptime_s=miner_elapsed,
            system_uptime_s=int(summary.get("Uptime", miner_elapsed)),
            hostname=info.get("hostname", ""),
            bos_version={
                "firmware": version.get("fw_ver", ""),
                "api": version.get("api_ver", ""),
                "platform": version.get("platform", ""),
                "chip": version.get("chip", ""),
                # Present only on newer firmware; the model otherwise comes from
                # devdetails (see WhatsminerCollector.fetch_identity).
                "miner_type": version.get("miner_type", ""),
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
    freq_mhz: Optional[float] = None


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
                freq_mhz=None,
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
                freq_mhz=None,
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
                freq_mhz=chain.get("freq"),
            ))
        return cls(hashboards=boards)

    @classmethod
    def from_bitmain(cls, raw: dict[str, Any]) -> HashboardData:
        stats = (raw.get("STATS") or [{}])[0]
        boards: list[HashboardReading] = []
        for chain in stats.get("chain", []):
            board_id = chain.get("index", 0)
            # temp_pcb is a list of 4 PCB sensor readings — take max for board_temp.
            pcb_temps = [
                float(t) for t in chain.get("temp_pcb", [])
                if isinstance(t, (int, float)) and t > 0
            ]
            board_temp: Optional[dict[str, Any]] = (
                {"value": max(pcb_temps), "unit": "C"} if pcb_temps else None
            )
            # temp_chip is a list of 4 ASIC die readings — take max.
            chip_temps = [
                float(t) for t in chain.get("temp_chip", [])
                if isinstance(t, (int, float)) and t > 0
            ]
            highest_chip: Optional[dict[str, Any]] = (
                {"value": max(chip_temps), "unit": "C"} if chip_temps else None
            )
            freq_avg = chain.get("freq_avg", 0)
            boards.append(HashboardReading(
                board_name=f"Chain {board_id}",
                board_temp=board_temp,
                highest_chip_temp=highest_chip,
                lowest_inlet_temp=None,
                highest_outlet_temp=None,
                chips_count=chain.get("asic_num", 0),
                id=str(board_id),
                enabled=chain.get("eeprom_loaded", False),
                stats={
                    "ghs_real": chain.get("rate_real", 0),
                    "ghs_ideal": chain.get("rate_ideal", 0),
                    "freq_avg": freq_avg,
                    "serial_number": chain.get("sn", ""),
                },
                freq_mhz=float(freq_avg) if freq_avg else None,
            ))
        return cls(hashboards=boards)

    @classmethod
    def from_sealminer(cls, stats_raw: dict[str, Any]) -> HashboardData:
        stats = (stats_raw.get("STATS") or [{}])[0]
        board_count = int(stats.get("Board Count", 0))
        boards: list[HashboardReading] = []
        for i in range(board_count):
            sensor_temps = [
                float(stats[f"{i} Temp {j}"])
                for j in range(4)
                if isinstance(stats.get(f"{i} Temp {j}"), (int, float)) and stats[f"{i} Temp {j}"] > 0
            ]
            # bdminer's per-board "{i} Temp {j}" sensors are on-die chip temps; it
            # exposes no separate board sensor, so the hottest of them is both the
            # board_temp and the highest_chip_temp. Populating highest_chip_temp is
            # required for analytics.miner_monthly_thermal (keyed on chip temp).
            hottest: Optional[dict[str, Any]] = (
                {"value": max(sensor_temps), "unit": "C"} if sensor_temps else None
            )
            board_temp = hottest
            freq = stats.get(f"{i} Freq")
            boards.append(HashboardReading(
                board_name=f"Board {i}",
                board_temp=board_temp,
                highest_chip_temp=hottest,
                lowest_inlet_temp=None,
                highest_outlet_temp=None,
                chips_count=int(stats.get(f"{i} Chip Count", 0)),
                id=str(i),
                enabled=bool(stats.get(f"{i} Online", False)),
                stats={
                    "mhs_av": stats.get(f"{i} MHS(Avg)", 0),
                    "mhs_1m": stats.get(f"{i} MHS(1m)", 0),
                    "mhs_5m": stats.get(f"{i} MHS(5m)", 0),
                    # Per-board nominal (bdminer "{i} MHS(Ideal)"), emitted as
                    # nominal_mhs so the pipeline's boardNominalGhs picks it up
                    # (matches LuxOS). Feeds the hashboard nominal fallback.
                    "nominal_mhs": stats.get(f"{i} MHS(Ideal)", 0),
                    "hardware_errors": stats.get(f"{i} HW", 0),
                    "serial_number": stats.get(f"{i} SN", ""),
                    "low_hash": stats.get(f"{i} Low Hash", False),
                    "tune_status": stats.get(f"{i} Tune Status", ""),
                    "bad_chip_count": stats.get(f"{i} Bad Chip Count", 0),
                },
                freq_mhz=float(freq) if freq is not None else None,
            ))
        return cls(hashboards=boards)

    @classmethod
    def from_whatsminer(cls, edevs_raw: dict[str, Any]) -> HashboardData:
        boards: list[HashboardReading] = []
        for dev in edevs_raw.get("DEVS", []):
            slot = dev.get("Slot", dev.get("ASC", 0))
            board_temp_val = dev.get("Temperature")
            chip_temp_val = dev.get("Chip Temp Max")
            freq = dev.get("Chip Frequency")
            boards.append(HashboardReading(
                board_name=f"Board {slot}",
                board_temp=(
                    {"value": board_temp_val, "unit": "C"} if board_temp_val is not None else None
                ),
                highest_chip_temp=(
                    {"value": chip_temp_val, "unit": "C"} if chip_temp_val is not None else None
                ),
                lowest_inlet_temp=None,
                highest_outlet_temp=None,
                # "Effective Chips" is the working chip count, which is what the
                # other adapters report as chips_count.
                chips_count=int(dev.get("Effective Chips", 0)),
                id=str(slot),
                enabled=dev.get("Enabled") == "Y" and dev.get("Status") == "Alive",
                stats={
                    "mhs_av": dev.get("MHS av", 0),
                    "mhs_5s": dev.get("MHS 5s", 0),
                    "mhs_1m": dev.get("MHS 1m", 0),
                    "mhs_5m": dev.get("MHS 5m", 0),
                    "mhs_15m": dev.get("MHS 15m", 0),
                    # Per-board "Factory GHS" is in GH/s; the pipeline's
                    # boardNominalGhs reads nominal_mhs, so convert to MH/s.
                    "nominal_mhs": dev.get("Factory GHS", 0) * 1000,
                    "accepted": dev.get("Accepted", 0),
                    "rejected": dev.get("Rejected", 0),
                    "chip_temp_min": dev.get("Chip Temp Min", 0),
                    "chip_temp_avg": dev.get("Chip Temp Avg", 0),
                    "serial_number": dev.get("PCB SN", ""),
                    "chip_data": dev.get("Chip Data", ""),
                    "upfreq_complete": dev.get("Upfreq Complete", 0),
                    "chip_vol_diff": dev.get("chip_vol_diff", 0),
                },
                freq_mhz=float(freq) if freq is not None else None,
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

    @classmethod
    def from_bitmain(cls, raw: dict[str, Any]) -> ErrorData:
        entries = [
            ErrorEntry(
                message=w.get("msg", ""),
                timestamp=w.get("timestamp", ""),
                error_codes=[{"code": w.get("code", ""), "level": w.get("level", "")}],
                components=[],
            )
            for w in raw.get("WARNINGS", [])
        ]
        return cls(errors=entries)

    @classmethod
    def from_sealminer(cls, stats_raw: dict[str, Any]) -> ErrorData:
        stats = (stats_raw.get("STATS") or [{}])[0]
        error_chip = str(stats.get("Error Chip", "")).strip()
        error_code = str(stats.get("Error Code", "")).strip()
        board_count = int(stats.get("Board Count", 0))
        hw_errors = sum(int(stats.get(f"{i} HW", 0)) for i in range(board_count))
        bad_chips = int(stats.get("Bad Chip Count", 0))
        # Only surface an error entry when there are real hardware failures.
        # "Error Code" is always populated (e.g. 602 on healthy machines) so
        # it is included as metadata only, not used as the trigger.
        if not error_chip and hw_errors == 0 and bad_chips == 0:
            return cls(errors=[])
        parts = []
        if error_chip:
            parts.append(f"Error chip: {error_chip}")
        if error_code:
            parts.append(f"Error code: {error_code}")
        msg = " | ".join(parts) if parts else f"HW errors: {hw_errors}, bad chips: {bad_chips}"
        return cls(errors=[ErrorEntry(
            message=msg,
            timestamp="",
            error_codes=[{"code": error_code}] if error_code else [],
            components=[{"chips": error_chip}] if error_chip else [],
        )])

    @classmethod
    def from_whatsminer(cls, error_raw: dict[str, Any]) -> ErrorData:
        """Map ``get_error_code``, whose entry shape varies across firmware.

        The healthy case is ``{"Msg": {"error_code": []}}``. When populated,
        btminer has shipped both a list of ``{"<code>": "<timestamp>",
        "reason": "..."}`` objects and a bare list/dict of codes, so both are
        handled rather than assuming one.
        """
        raw_codes = (error_raw.get("Msg") or {}).get("error_code")
        if isinstance(raw_codes, dict):
            raw_codes = [{k: v} for k, v in raw_codes.items()]
        entries: list[ErrorEntry] = []
        for item in raw_codes or []:
            if isinstance(item, dict):
                reason = str(item.get("reason", ""))
                # Every non-"reason" key is a code whose value is its timestamp.
                codes = {str(k): v for k, v in item.items() if k != "reason"}
                code = next(iter(codes), "")
                entries.append(ErrorEntry(
                    message=reason or f"Error code {code}",
                    timestamp=str(codes.get(code, "")),
                    error_codes=[{"code": c} for c in codes],
                    components=[],
                ))
            else:
                entries.append(ErrorEntry(
                    message=f"Error code {item}",
                    timestamp="",
                    error_codes=[{"code": str(item)}],
                    components=[],
                ))
        return cls(errors=entries)


# ---------------------------------------------------------------------------
# Scan snapshot — facility-level topology sent once per poll cycle
# ---------------------------------------------------------------------------


@dataclass
class SubnetScanSummary:
    cidr: str
    miners: list[str]  # canonical miner UIDs: "<facility_id>:<normalized_mac>"


@dataclass
class ScanSummaryData:
    facility_id: str
    timestamp: str
    subnets: list[SubnetScanSummary]
    total_miners: int
