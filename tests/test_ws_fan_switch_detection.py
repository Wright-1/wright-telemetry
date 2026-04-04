"""WebSocket fan switch detection (_check_fan_rpm_changes)."""

from __future__ import annotations

from wright_telemetry.models import CoolingData, FanReading
from wright_telemetry.scheduler import _WS_FAN_RPM_RUNNING_THRESHOLD, _check_fan_rpm_changes


def _cooling(rpms: list[int]) -> CoolingData:
    return CoolingData(
        fans=[
            FanReading(position=i + 1, rpm=rpm, target_speed_ratio=0.0)
            for i, rpm in enumerate(rpms)
        ],
    )


def test_first_sample_seeds_prev_no_event() -> None:
    prev: dict = {}
    ev = _check_fan_rpm_changes("m1", _cooling([3000]), "http://x", prev, [])
    assert ev == []


def test_running_to_stopped_emits_off() -> None:
    prev = {("http://x", 1): 3000}
    ev = _check_fan_rpm_changes("m1", _cooling([0]), "http://x", prev, [])
    assert len(ev) == 1
    assert ev[0]["transition_type"] == "off"


def test_stopped_to_running_emits_on() -> None:
    prev = {("http://x", 1): 0}
    t = _WS_FAN_RPM_RUNNING_THRESHOLD + 100
    ev = _check_fan_rpm_changes("m1", _cooling([t]), "http://x", prev, [])
    assert len(ev) == 1
    assert ev[0]["transition_type"] == "on"
