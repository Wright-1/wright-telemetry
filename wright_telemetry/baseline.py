"""Fan baseline tracking per machine + fan position.

Computes a clean operating baseline (avg RPM, avg chip temp, std devs) after a
warmup window and a minimum number of healthy samples.  State is persisted to
~/.wright-telemetry/baselines.json so progress survives service restarts.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

from wright_telemetry.models import CoolingData, MinerIdentity

BASELINE_FILE = Path.home() / ".wright-telemetry" / "baselines.json"

# How long after first reading to discard samples (warmup period).
# Middle of the 30–60 min spec window.
_WARMUP_SECONDS = 2700  # 45 minutes

# Minimum healthy samples required before computing the baseline.
# At a 30 s poll interval this represents ~15 minutes of data.
_MIN_SAMPLES = 30

# Chip temp ceiling for a "healthy" reading (°C).
_HEALTHY_TEMP_CEIL = 85.0

# Minimum RPM for a fan to be considered running during baseline collection.
_HEALTHY_RPM_MIN = 100


@dataclass
class BaselineRecord:
    machine_id: str
    fan_position: int
    baseline_rpm: float
    baseline_rpm_stddev: float
    baseline_temp: Optional[float]
    baseline_temp_stddev: Optional[float]
    baseline_created_at: str
    baseline_sample_count: int
    baseline_start_time: str
    baseline_end_time: str
    # Average efficiency = avg_rpm / avg_target_rpm, reflecting how closely the
    # fan tracked its speed target during the baseline window.
    baseline_efficiency: Optional[float] = None
    # Average target RPM observed during the baseline window.
    baseline_target_rpm: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_pipeline_dict(self) -> dict[str, Any]:
        """Return the payload format expected by the data-pipeline handleBaseline().

        The pipeline handler expects::

            {
                "baselines": [
                    {
                        "fan_position": int,
                        "avg_rpm":       float,
                        "rpm_stddev":    float,
                        "avg_chip_temp": float | null,
                        "temp_stddev":   float | null,
                        "efficiency":    float | null,
                        "sample_count":  int,
                    }
                ],
                "window_start": str,   # ISO-8601
                "window_end":   str,   # ISO-8601
            }
        """
        return {
            "baselines": [
                {
                    "fan_position": self.fan_position,
                    "avg_rpm": self.baseline_rpm,
                    "rpm_stddev": self.baseline_rpm_stddev,
                    "avg_chip_temp": self.baseline_temp,
                    "temp_stddev": self.baseline_temp_stddev,
                    "efficiency": self.baseline_efficiency,
                    "sample_count": self.baseline_sample_count,
                }
            ],
            "window_start": self.baseline_start_time,
            "window_end": self.baseline_end_time,
        }


class BaselineTracker:
    """Tracks per-(machine_id, fan_position) clean operating baselines.

    State layout in baselines.json (key = "{uid}:{fan_position}"):
        {
          "uid1:0": {
            "first_seen": <unix timestamp>,
            "samples": [[ts, rpm, temp_or_null], ...],  // cleared after baseline
            "baseline": { ...BaselineRecord fields... } | null
          }
        }
    """

    def __init__(
        self,
        warmup_seconds: int = _WARMUP_SECONDS,
        min_samples: int = _MIN_SAMPLES,
        healthy_temp_ceil: float = _HEALTHY_TEMP_CEIL,
        healthy_rpm_min: int = _HEALTHY_RPM_MIN,
        state_file: Path = BASELINE_FILE,
    ):
        self._warmup_seconds = warmup_seconds
        self._min_samples = min_samples
        self._healthy_temp_ceil = healthy_temp_ceil
        self._healthy_rpm_min = healthy_rpm_min
        self._state_file = state_file
        self._state: dict[str, dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._state_file.exists():
            try:
                with open(self._state_file) as f:
                    self._state = json.load(f)
            except Exception:
                self._state = {}

    def _save(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._state_file, "w") as f:
            json.dump(self._state, f, indent=2)

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def record(
        self,
        identity: MinerIdentity,
        cooling: CoolingData,
    ) -> list[BaselineRecord]:
        """Process one cooling reading.

        Returns a list of BaselineRecord objects that were *newly* established
        during this call (usually empty; one entry when the threshold is hit).
        """
        now = time.time()
        # Prefer the per-fan chip_temp (added in FanReading v2); fall back to
        # the miner-level highest_temperature so older collectors still work.
        global_chip_temp: Optional[float] = (
            cooling.highest_temperature.get("value")
            if cooling.highest_temperature
            else None
        )

        newly_established: list[BaselineRecord] = []

        for fan in cooling.fans:
            # Use per-fan chip_temp when available, otherwise the global value.
            chip_temp: Optional[float] = (
                fan.chip_temp if getattr(fan, "chip_temp", None) is not None
                else global_chip_temp
            )
            target_rpm: Optional[float] = getattr(fan, "target_rpm", None)

            key = f"{identity.uid}:{fan.position}"
            entry = self._state.setdefault(key, {
                "first_seen": now,
                "samples": [],
                "baseline": None,
            })

            # Already established — nothing to do.
            if entry.get("baseline") is not None:
                continue

            # Still inside the warmup window — discard.
            if now - entry["first_seen"] < self._warmup_seconds:
                continue

            # Only accumulate healthy readings.
            if fan.rpm < self._healthy_rpm_min:
                continue
            if chip_temp is not None and chip_temp >= self._healthy_temp_ceil:
                continue

            # Each sample: [timestamp, rpm, chip_temp, target_rpm]
            entry["samples"].append([now, fan.rpm, chip_temp, target_rpm])
            self._save()

            if len(entry["samples"]) >= self._min_samples:
                baseline = self._compute_baseline(
                    identity.uid, fan.position, entry["samples"]
                )
                entry["baseline"] = baseline.to_dict()
                entry["samples"] = []  # no longer needed
                self._save()
                newly_established.append(baseline)

        return newly_established

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_baseline(
        self, machine_id: str, fan_position: int
    ) -> Optional[BaselineRecord]:
        """Return the established baseline for a key, or None."""
        entry = self._state.get(f"{machine_id}:{fan_position}")
        if entry and entry.get("baseline"):
            return BaselineRecord(**entry["baseline"])
        return None

    def warmup_remaining(self, machine_id: str, fan_position: int) -> Optional[float]:
        """Seconds left in the warmup window, or None if key unknown."""
        entry = self._state.get(f"{machine_id}:{fan_position}")
        if entry is None:
            return None
        return max(0.0, self._warmup_seconds - (time.time() - entry["first_seen"]))

    def status_summary(
        self, machine_id: str, fan_position: int
    ) -> dict[str, Any]:
        """Return a dashboard-ready status dict for a fan position."""
        entry = self._state.get(f"{machine_id}:{fan_position}")
        if entry is None:
            return {"baseline_established": False, "baseline_sample_count": 0}

        b = entry.get("baseline")
        if b:
            return {
                "baseline_established": True,
                "baseline_sample_count": b["baseline_sample_count"],
                "baseline_start_time": b["baseline_start_time"],
                "baseline_end_time": b["baseline_end_time"],
                "baseline_rpm": b["baseline_rpm"],
                "baseline_rpm_stddev": b["baseline_rpm_stddev"],
                "baseline_temp": b.get("baseline_temp"),
                "baseline_temp_stddev": b.get("baseline_temp_stddev"),
                "baseline_created_at": b["baseline_created_at"],
            }

        remaining = self.warmup_remaining(machine_id, fan_position)
        return {
            "baseline_established": False,
            "baseline_sample_count": len(entry.get("samples", [])),
            "warmup_remaining_s": remaining,
        }

    # ------------------------------------------------------------------
    # Internal computation
    # ------------------------------------------------------------------

    def _compute_baseline(
        self,
        machine_id: str,
        fan_position: int,
        samples: list[list],
    ) -> BaselineRecord:
        rpms = [s[1] for s in samples]
        # Support both 3-element [ts, rpm, temp] and 4-element [ts, rpm, temp, target_rpm].
        temps = [s[2] for s in samples if s[2] is not None]
        target_rpms = [s[3] for s in samples if len(s) > 3 and s[3] is not None]
        timestamps = [s[0] for s in samples]

        avg_rpm = sum(rpms) / len(rpms)
        rpm_stddev = math.sqrt(sum((r - avg_rpm) ** 2 for r in rpms) / len(rpms))

        avg_temp: Optional[float] = None
        temp_stddev: Optional[float] = None
        if temps:
            avg_temp = sum(temps) / len(temps)
            temp_stddev = math.sqrt(
                sum((t - avg_temp) ** 2 for t in temps) / len(temps)
            )

        avg_target_rpm: Optional[float] = None
        efficiency: Optional[float] = None
        if target_rpms:
            avg_target_rpm = sum(target_rpms) / len(target_rpms)
            if avg_target_rpm > 0:
                efficiency = round(avg_rpm / avg_target_rpm, 4)

        return BaselineRecord(
            machine_id=machine_id,
            fan_position=fan_position,
            baseline_rpm=round(avg_rpm, 2),
            baseline_rpm_stddev=round(rpm_stddev, 2),
            baseline_temp=round(avg_temp, 2) if avg_temp is not None else None,
            baseline_temp_stddev=(
                round(temp_stddev, 2) if temp_stddev is not None else None
            ),
            baseline_created_at=time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
            baseline_sample_count=len(samples),
            baseline_start_time=time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(min(timestamps))
            ),
            baseline_end_time=time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(max(timestamps))
            ),
            baseline_efficiency=efficiency,
            baseline_target_rpm=round(avg_target_rpm, 1) if avg_target_rpm else None,
        )
