"""Single fake miner server — one process per Docker container.

Controlled entirely by environment variables:

    FIRMWARE      braiins | vnish | luxos      (default: braiins)
    MINER_INDEX   integer 0-255               (default: 0)
    FIXTURES_DIR  path to fixtures root       (default: /fixtures)
    HTTP_PORT     port for Braiins/Vnish      (default: 80)
    LUXOS_PORT    port for LuxOS TCP API      (default: 4028)
    CONTROL_PORT  port for the fan-control    (default: 8080)
                  HTTP server (all firmware)

Each miner gets a unique hostname, MAC address, serial number, uid, and
jittered hashrate derived deterministically from MINER_INDEX so the numbers
are stable across container restarts.

LIVE TELEMETRY SIMULATION
--------------------------
All metrics vary over time to mimic a real miner in normal operation:

  Fans         : sinusoidal oscillation ±4 %, 60 s period (FanState)
  Hashrate     : sinusoidal oscillation ±3 %, 120 s period
  Temperatures : per-board sinusoidal oscillation ±3 °C,  90 s period
  Power        : sinusoidal oscillation ±2.5 %, 150 s period
  PSU voltage  : sinusoidal oscillation ±0.4 %, 45 s period
  Fan targets  : slow tracking of temperature (PI controller behaviour)
  Share counts : accepted/rejected/stale accumulate at realistic rates
  Uptime       : increments in real time from fixture base value
  Total MH     : accumulates based on current hashrate × elapsed time
  HW errors    : very slowly increment (~4/hour across all boards)

FAN RPM SIMULATION
------------------
Fan RPMs are not static — they oscillate slowly (±4 %, 60 s period) around
their base values and can be driven to 0 to simulate a Wright Fan swap:

    GET  http://<container>:<CONTROL_PORT>/     → JSON fan state
    POST http://<container>:<CONTROL_PORT>/
         {"action": "fan_dip",    "duration_s": 8}   all fans → 0 for 8 s
         {"action": "fan_restore"}                    cancel dip early

For Braiins and Vnish containers the /control route is also available on the
main HTTP port:
    GET  http://<container>:<HTTP_PORT>/control
    POST http://<container>:<HTTP_PORT>/control

AUTHENTICATION
--------------
The server enforces token-based auth exactly as real hardware does:
  • Before any login request arrives, all traffic is allowed.
  • After the first successful POST to /api/v1/auth/login (Braiins) or
    /api/v1/unlock (Vnish), every subsequent GET must carry the correct
    token header.  Missing or wrong token → HTTP 401, which exercises the
    collector's re-auth / retry path.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import random
import socket
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("fake-miner")

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

FIRMWARE     = os.environ.get("FIRMWARE",     "braiins").lower().strip()
MINER_INDEX  = int(os.environ.get("MINER_INDEX",  "0"))
FIXTURES_DIR = Path(os.environ.get("FIXTURES_DIR", "/fixtures"))
HTTP_PORT    = int(os.environ.get("HTTP_PORT",    "80"))
LUXOS_PORT   = int(os.environ.get("LUXOS_PORT",   "4028"))
CONTROL_PORT = int(os.environ.get("CONTROL_PORT", "8080"))


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

def load_fixtures() -> dict[str, Any]:
    folder = FIXTURES_DIR / FIRMWARE
    if not folder.is_dir():
        logger.error(
            "Fixtures directory not found: %s — is the volume mounted?", folder
        )
        sys.exit(1)
    out: dict[str, Any] = {}
    for path in sorted(folder.glob("*.json")):
        with path.open() as fh:
            out[path.stem] = json.load(fh)
    logger.info("Loaded %d fixture(s) from %s", len(out), folder)
    return out


# ---------------------------------------------------------------------------
# FanState – live RPM simulation
# ---------------------------------------------------------------------------

class FanState:
    """Per-fake-miner fan RPM controller.

    Normal mode : slow sinusoidal oscillation (±4 %, 60 s period) around
                  each fan's base RPM; MINER_INDEX phase-shifts each container
                  so they don't all peak at the same moment.
    Dip mode    : all fans drop to 0 RPM for *duration_s* seconds, then
                  ramp back up linearly over ~2 s.
    """

    _PERIOD_S  = 60.0   # sinusoidal period in seconds
    _AMPLITUDE = 0.04   # ±4 % amplitude

    def __init__(self, base_rpms: dict[int, int], idx: int) -> None:
        self._base      = dict(base_rpms)
        self._phase     = (idx * 0.37) % (2 * math.pi)
        self._dip_until = 0.0
        self._lock      = threading.Lock()

    def current_rpm(self, position: int) -> int:
        base = self._base.get(position, 0)
        if base == 0:
            return 0
        with self._lock:
            dip_until = self._dip_until
        now = time.time()
        if now < dip_until:
            return 0
        # Soft ramp-up over 2 s after a dip; at startup dip_until==0 → recovery==1
        recovery = min(1.0, (now - dip_until) / 2.0) if dip_until > 0 else 1.0
        osc = math.sin(now * 2 * math.pi / self._PERIOD_S + self._phase)
        return max(0, round(base * recovery * (1.0 + osc * self._AMPLITUDE)))

    def in_dip(self) -> bool:
        with self._lock:
            return time.time() < self._dip_until

    def trigger_dip(self, duration_s: float = 8.0) -> None:
        with self._lock:
            self._dip_until = time.time() + duration_s
        logger.info("FanState: dip triggered for %.0f s", duration_s)

    def trigger_restore(self) -> None:
        with self._lock:
            self._dip_until = 0.0
        logger.info("FanState: restored to normal")

    def status(self) -> dict[str, Any]:
        with self._lock:
            remaining = max(0.0, self._dip_until - time.time())
        return {
            "mode":            "dip" if remaining > 0 else "normal",
            "dip_remaining_s": round(remaining, 1),
            "base_rpms":       dict(self._base),
        }


# ---------------------------------------------------------------------------
# LiveMinerState – time-varying simulation of all non-fan telemetry
# ---------------------------------------------------------------------------

class LiveMinerState:
    """Realistic time-varying simulation of all miner telemetry except fan RPMs.

    Oscillation
    -----------
    Hashrate   : ±3 %,   120 s period
    Temperature: ±3 °C,   90 s period (each board has a slight phase offset)
    Power      : ±2.5 %, 150 s period
    PSU voltage: ±0.4 %,  45 s period

    Fan speed target tracks temperature with a slow PI-like response
    (real miner fan controllers do the same thing).

    Counters
    --------
    accepted, rejected, stale, hardware_errors and total_mh all accumulate
    at realistic rates relative to base values in the personalised fixtures,
    giving a continuously rising trend just like a real machine.
    """

    # --- oscillation parameters ---
    _HASH_AMP    = 0.030   # ±3 %
    _HASH_PERIOD = 120.0   # 2-minute cycle

    _TEMP_AMP_C  = 3.0     # ±3 °C absolute
    _TEMP_PERIOD = 90.0

    _POWER_AMP    = 0.025  # ±2.5 %
    _POWER_PERIOD = 150.0

    _VOLT_AMP    = 0.004   # ±0.4 %
    _VOLT_PERIOD = 45.0

    # Fan target tracks temperature: ±(TEMP_AMP_C * FAN_TARGET_GAIN) in ratio units
    # At +3 °C chip temp the fan target increases by ~1.5 percentage points.
    _FAN_TARGET_GAIN = 0.005  # ratio per °C

    # --- accumulation rates (realistic S19-class miner) ---
    _ACCEPTED_RATE  = 8.5    # shares / s  ≈ 30 600 / hr
    _REJECTED_RATE  = 0.003  # shares / s  ≈     11 / hr
    _STALE_RATE     = 0.001  # shares / s  ≈      4 / hr
    _HW_ERR_RATE    = 0.001  # errors / s  ≈      4 / hr  (total, split across boards)

    # Typical pool difficulty per accepted share (used to update difficulty_accepted)
    _SHARE_DIFFICULTY = 16_000

    def __init__(self, idx: int) -> None:
        self._start = time.time()
        # Global phase for all oscillators (shifted per-miner so containers differ)
        self._phase = (idx * 0.37) % (2 * math.pi)
        # Per-board phases so boards don't all peak at the same moment
        self._board_phases = [
            (idx * 0.37 + i * 1.1) % (2 * math.pi) for i in range(4)
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sin(self, period: float, extra_phase: float = 0.0) -> float:
        """Normalised sin oscillator in [-1, 1]."""
        return math.sin(time.time() * 2 * math.pi / period + self._phase + extra_phase)

    def elapsed(self) -> int:
        """Seconds since the server started."""
        return int(time.time() - self._start)

    # ------------------------------------------------------------------
    # Oscillating factors / absolute deltas
    # ------------------------------------------------------------------

    def hashrate_factor(self) -> float:
        """Multiplier for instantaneous hashrate readings (5 s window)."""
        return 1.0 + self._HASH_AMP * self._sin(self._HASH_PERIOD)

    def hashrate_factor_30m(self) -> float:
        """Multiplier for 30-minute averaged hashrate (heavily dampened)."""
        return 1.0 + self._HASH_AMP * 0.25 * self._sin(self._HASH_PERIOD)

    def hashrate_factor_av(self) -> float:
        """Multiplier for lifetime average hashrate (very stable)."""
        return 1.0 + self._HASH_AMP * 0.08 * self._sin(self._HASH_PERIOD)

    def temp_delta(self, board_idx: int = 0) -> float:
        """Absolute temperature delta in °C for board *board_idx*."""
        phase = self._board_phases[board_idx % 4]
        return self._TEMP_AMP_C * math.sin(
            time.time() * 2 * math.pi / self._TEMP_PERIOD + phase
        )

    def power_factor(self) -> float:
        """Multiplier for power consumption readings."""
        return 1.0 + self._POWER_AMP * self._sin(self._POWER_PERIOD)

    def voltage(self, base: float) -> float:
        """Current PSU voltage given a base voltage."""
        return round(base * (1.0 + self._VOLT_AMP * self._sin(self._VOLT_PERIOD)), 3)

    def fan_target_delta(self, board_idx: int = 0) -> float:
        """Delta to add to fan target speed ratio (tracks temperature)."""
        return self.temp_delta(board_idx) * self._FAN_TARGET_GAIN

    # ------------------------------------------------------------------
    # Accumulating counters (add these to base fixture values)
    # ------------------------------------------------------------------

    def accepted_delta(self) -> int:
        return round(self.elapsed() * self._ACCEPTED_RATE)

    def rejected_delta(self) -> int:
        return round(self.elapsed() * self._REJECTED_RATE)

    def stale_delta(self) -> int:
        return round(self.elapsed() * self._STALE_RATE)

    def hw_errors_delta(self) -> int:
        return round(self.elapsed() * self._HW_ERR_RATE)

    def difficulty_accepted_delta(self) -> float:
        return self.accepted_delta() * self._SHARE_DIFFICULTY

    def total_mh_delta(self, base_ghs: float) -> float:
        """Additional MH accumulated since server start at the current hashrate."""
        return base_ghs * 1000.0 * self.elapsed()


# ---------------------------------------------------------------------------
# Per-miner personalisation
# ---------------------------------------------------------------------------

def _mac(offset: int) -> str:
    """Derive a unique-looking MAC from MINER_INDEX + an offset."""
    n = (MINER_INDEX + offset) & 0xFFFFFF
    return f"AA:BB:CC:{(n >> 16) & 0xFF:02X}:{(n >> 8) & 0xFF:02X}:{n & 0xFF:02X}"


def _jitter(value: float, pct: float = 0.05) -> float:
    """Slightly randomise *value* — caller must seed random first."""
    return round(value * (1.0 + random.uniform(-pct, pct)), 3)


def personalize(fixtures: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of *fixtures* with per-miner identity + jittered metrics."""
    random.seed(MINER_INDEX)  # deterministic: same index → same numbers on every restart
    fx = copy.deepcopy(fixtures)

    if FIRMWARE == "braiins":
        details = fx.get("miner_details", {})
        details["hostname"]      = f"braiins-fake-{MINER_INDEX:03d}"
        details["mac_address"]   = _mac(0x000000)
        details["serial_number"] = f"BRFAKE{MINER_INDEX:05d}"
        details["uid"]           = f"brfake{MINER_INDEX:012x}"

        # Jitter real_hashrate / nominal_hashrate.
        inner = fx.get("miner_stats", {}).get("miner_stats", {})
        for key in ("real_hashrate", "nominal_hashrate"):
            node = inner.get(key)
            if isinstance(node, dict) and "gigahash_per_second" in node:
                node["gigahash_per_second"] = _jitter(node["gigahash_per_second"])

    elif FIRMWARE == "vnish":
        info = fx.get("info", {})
        info["hostname"] = f"vnish-fake-{MINER_INDEX:03d}"
        info["mac"]      = _mac(0x001000)
        info["serial"]   = f"VNFAKE{MINER_INDEX:05d}"
        info["uid"]      = f"vnfake{MINER_INDEX:012x}"

        inner = fx.get("summary", {}).get("miner", {})
        for key in ("instant_hashrate", "average_hashrate"):
            if key in inner:
                inner[key] = _jitter(inner[key])

    elif FIRMWARE == "luxos":
        cfg_list = fx.get("config", {}).get("CONFIG")
        if isinstance(cfg_list, list) and cfg_list:
            cfg_list[0]["Hostname"]     = f"luxos-fake-{MINER_INDEX:03d}"
            cfg_list[0]["MACAddr"]      = _mac(0x002000)
            cfg_list[0]["SerialNumber"] = f"LXFAKE{MINER_INDEX:05d}"

        summary_list = fx.get("summary", {}).get("SUMMARY")
        if isinstance(summary_list, list) and summary_list:
            for key in ("GHS 5s", "GHS 30m", "GHS av"):
                if key in summary_list[0]:
                    summary_list[0][key] = _jitter(summary_list[0][key])

    return fx


# ---------------------------------------------------------------------------
# Live injection helpers – Braiins
# ---------------------------------------------------------------------------

def _inject_braiins_miner_details(
    payload: dict[str, Any], live: LiveMinerState
) -> dict[str, Any]:
    """Increment uptime counters."""
    payload = copy.deepcopy(payload)
    elapsed = live.elapsed()
    payload["bosminer_uptime_s"] = payload.get("bosminer_uptime_s", 0) + elapsed
    payload["system_uptime_s"]   = payload.get("system_uptime_s", 0) + elapsed
    return payload


def _inject_braiins_cooling_state(
    payload: dict[str, Any], fan_state: FanState, live: LiveMinerState
) -> dict[str, Any]:
    """Inject live fan RPMs, fan target ratios, and highest chip temperature."""
    payload = copy.deepcopy(payload)

    td = live.temp_delta(0)

    for f in payload.get("fans", []):
        pos = f["position"]
        f["rpm"] = fan_state.current_rpm(pos)
        base_ratio = f.get("target_speed_ratio", 0.65)
        f["target_speed_ratio"] = round(
            max(0.0, min(1.0, base_ratio + live.fan_target_delta(0))), 3
        )

    ht = payload.get("highest_temperature")
    if isinstance(ht, dict) and "value" in ht:
        ht["value"] = round(ht["value"] + td, 1)

    return payload


def _inject_braiins_miner_stats(
    payload: dict[str, Any], live: LiveMinerState
) -> dict[str, Any]:
    """Oscillate hashrate / power and accumulate share counters."""
    payload = copy.deepcopy(payload)

    ms = payload.get("miner_stats", {})
    hf = live.hashrate_factor()

    # Hashrate
    rh = ms.get("real_hashrate", {})
    base_ghs = rh.get("gigahash_per_second", 145_000.0)
    current_ghs = round(base_ghs * hf, 1)
    rh["gigahash_per_second"] = current_ghs

    # Derived miner stats
    ms["hardware_errors"] = ms.get("hardware_errors", 0) + live.hw_errors_delta()
    ms["utility"]         = round(ms.get("utility", 8.45) * hf, 2)
    ms["work_utility"]    = round(ms.get("work_utility", 0.0) * hf, 1)
    ms["total_mh"]        = round(
        ms.get("total_mh", 0) + live.total_mh_delta(base_ghs)
    )

    # Power
    ps = payload.get("power_stats", {})
    pf = live.power_factor()
    base_w = ps.get("watts", 3245)
    current_w = round(base_w * pf)
    ps["watts"]       = current_w
    ps["psu_voltage"] = live.voltage(ps.get("psu_voltage", 12.1))
    if current_ghs > 0:
        # J/TH = watts / (GH/s / 1000)
        ps["efficiency"] = round(current_w / (current_ghs / 1000.0), 2)

    # Pool share counters
    for p in payload.get("pool_stats", {}).get("pools", []):
        base_acc  = p.get("accepted", 0)
        base_rej  = p.get("rejected", 0)
        new_acc   = base_acc + live.accepted_delta()
        new_rej   = base_rej + live.rejected_delta()
        p["accepted"]            = new_acc
        p["rejected"]            = new_rej
        p["stale"]               = p.get("stale", 0) + live.stale_delta()
        p["difficulty_accepted"] = round(
            p.get("difficulty_accepted", 0) + live.difficulty_accepted_delta()
        )
        total = new_acc + new_rej
        if total > 0:
            p["pool_rejected_pct"] = round(new_rej / total * 100, 3)

    return payload


def _inject_braiins_hashboards(
    payload: dict[str, Any], live: LiveMinerState
) -> dict[str, Any]:
    """Vary per-board temperatures and hashrates; accumulate per-board counters."""
    payload = copy.deepcopy(payload)
    boards = payload.get("hashboards", [])
    n = max(len(boards), 1)
    hf = live.hashrate_factor()

    for i, b in enumerate(boards):
        td = live.temp_delta(i)

        # Temperatures (stored as {"value": X, "unit": "C"} dicts)
        scales = {
            "board_temp":          1.00,
            "highest_chip_temp":   1.00,
            "lowest_inlet_temp":   0.30,  # inlet is more stable
            "highest_outlet_temp": 0.70,
        }
        for key, scale in scales.items():
            t = b.get(key)
            if isinstance(t, dict) and "value" in t:
                t["value"] = round(t["value"] + td * scale, 1)

        # Per-board hashrate
        stats = b.get("stats", {})
        for key in ("ghs_5s",):
            if key in stats:
                stats[key] = round(stats[key] * hf, 1)
        if "ghs_30m" in stats:
            stats["ghs_30m"] = round(stats["ghs_30m"] * live.hashrate_factor_30m(), 1)

        # Per-board counters (distribute total delta evenly across boards)
        stats["accepted"]        = stats.get("accepted", 0) + live.accepted_delta() // n
        stats["rejected"]        = stats.get("rejected", 0) + live.rejected_delta() // n
        stats["hardware_errors"] = stats.get("hardware_errors", 0) + live.hw_errors_delta() // n

    return payload


# ---------------------------------------------------------------------------
# Live injection helpers – VNish
# ---------------------------------------------------------------------------

def _inject_vnish_summary(
    payload: dict[str, Any], live: LiveMinerState
) -> dict[str, Any]:
    """Oscillate hashrate / power and accumulate share / uptime counters."""
    payload = copy.deepcopy(payload)

    miner = payload.get("miner", {})
    hf = live.hashrate_factor()
    base_instant = miner.get("instant_hashrate", 145_000.0)
    base_average  = miner.get("average_hashrate", 145_000.0)

    miner["instant_hashrate"] = round(base_instant * hf, 1)
    miner["average_hashrate"] = round(base_average * live.hashrate_factor_30m(), 1)
    miner["hardware_errors"]  = miner.get("hardware_errors", 0) + live.hw_errors_delta()
    miner["uptime"]           = miner.get("uptime", 0) + live.elapsed()
    if miner.get("hr_nominal"):
        pass  # nominal stays fixed (it's the rated spec)

    # Power
    power = payload.get("power", {})
    pf = live.power_factor()
    base_w = power.get("watts", 3245)
    current_w = round(base_w * pf)
    power["watts"]             = current_w
    power["power_consumption"] = current_w + 5   # DC draw is typically ~5 W higher
    if base_instant > 0:
        power["efficiency"] = round(current_w / (miner["instant_hashrate"] / 1000.0), 2)

    # Pool counters
    for p in payload.get("pools", []):
        new_acc = p.get("accepted", 0) + live.accepted_delta()
        new_rej = p.get("rejected", 0) + live.rejected_delta()
        p["accepted"]            = new_acc
        p["rejected"]            = new_rej
        p["stale"]               = p.get("stale", 0) + live.stale_delta()
        p["difficulty_accepted"] = round(
            p.get("difficulty_accepted", 0) + live.difficulty_accepted_delta()
        )
        total = new_acc + new_rej
        if total > 0:
            p["pool_rejected_pct"] = round(new_rej / total * 100, 3)

    return payload


def _inject_vnish_status(
    payload: dict[str, Any], fan_state: FanState, live: LiveMinerState
) -> dict[str, Any]:
    """Inject live fan RPMs + target speeds, chain temperatures, and per-chain counters."""
    payload = copy.deepcopy(payload)

    # Fans: RPM from FanState; speed_pct tracks temperature
    for f in payload.get("fans", []):
        fid = f["id"]
        f["rpm"] = fan_state.current_rpm(fid)
        base_pct = f.get("speed_pct", 65)
        f["speed_pct"] = max(
            0, min(100, round(base_pct + live.fan_target_delta(0) * 100))
        )

    chains = payload.get("chains", [])
    n = max(len(chains), 1)
    hf = live.hashrate_factor()

    for i, c in enumerate(chains):
        td = live.temp_delta(i)
        if "temp_board" in c:
            c["temp_board"] = round(c["temp_board"] + td, 1)
        if "temp_chip" in c:
            c["temp_chip"] = round(c["temp_chip"] + td, 1)
        if "hashrate" in c:
            c["hashrate"] = round(c["hashrate"] * hf, 1)
        c["accepted"]  = c.get("accepted", 0) + live.accepted_delta() // n
        c["rejected"]  = c.get("rejected", 0) + live.rejected_delta() // n
        c["hw_errors"] = c.get("hw_errors", 0) + live.hw_errors_delta() // n

    return payload


# ---------------------------------------------------------------------------
# Live injection helpers – LuxOS
# ---------------------------------------------------------------------------

def _inject_luxos_fans(payload: dict[str, Any], fan_state: FanState) -> dict[str, Any]:
    payload = copy.deepcopy(payload)
    for f in payload.get("FANS", []):
        f["RPM"] = fan_state.current_rpm(f["ID"])
    return payload


def _inject_luxos_temps(
    payload: dict[str, Any], live: LiveMinerState
) -> dict[str, Any]:
    """Vary per-board temperatures across all thermal sensors."""
    payload = copy.deepcopy(payload)
    now_epoch = int(time.time())
    for s in payload.get("STATUS", []):
        s["When"] = now_epoch

    for t in payload.get("TEMPS", []):
        board_idx = t.get("ID", 0)
        td = live.temp_delta(board_idx)
        for key, scale in [
            ("Board",       1.00),
            ("Chip",        1.00),
            ("TopLeft",     0.30),
            ("TopRight",    0.30),
            ("BottomLeft",  0.70),
            ("BottomRight", 0.70),
        ]:
            if key in t:
                t[key] = round(t[key] + td * scale, 1)

    return payload


def _inject_luxos_summary(
    payload: dict[str, Any], live: LiveMinerState
) -> dict[str, Any]:
    """Oscillate hashrate metrics and accumulate share / uptime counters."""
    payload = copy.deepcopy(payload)
    now_epoch = int(time.time())
    for s in payload.get("STATUS", []):
        s["When"] = now_epoch

    summary_list = payload.get("SUMMARY", [])
    if not summary_list:
        return payload
    sm = summary_list[0]

    base_ghs_5s = sm.get("GHS 5s", 145_000.0)
    base_ghs_30m = sm.get("GHS 30m", base_ghs_5s)
    base_ghs_av  = sm.get("GHS av",  base_ghs_5s)

    current_ghs_5s = round(base_ghs_5s * live.hashrate_factor(), 1)
    sm["GHS 5s"]  = current_ghs_5s
    sm["GHS 30m"] = round(base_ghs_30m * live.hashrate_factor_30m(), 1)
    sm["GHS av"]  = round(base_ghs_av  * live.hashrate_factor_av(), 1)

    sm["Total MH"]       = round(sm.get("Total MH", 0) + live.total_mh_delta(base_ghs_5s))
    sm["Hardware Errors"]= sm.get("Hardware Errors", 0) + live.hw_errors_delta()
    sm["Utility"]        = round(sm.get("Utility", 8.45) * live.hashrate_factor(), 2)
    sm["Work Utility"]   = round(sm.get("Work Utility", 0.0) * live.hashrate_factor(), 1)
    sm["Elapsed"]        = sm.get("Elapsed", 0) + live.elapsed()

    new_acc = sm.get("Accepted", 0) + live.accepted_delta()
    new_rej = sm.get("Rejected", 0) + live.rejected_delta()
    sm["Accepted"]         = new_acc
    sm["Rejected"]         = new_rej
    sm["Stale"]            = sm.get("Stale", 0) + live.stale_delta()
    sm["Best Share"]       = sm.get("Best Share", 0)  # static — rare lucky event

    return payload


def _inject_luxos_devs(
    payload: dict[str, Any], live: LiveMinerState
) -> dict[str, Any]:
    """Vary per-device hashrate, temperature, and counters."""
    payload = copy.deepcopy(payload)
    now_epoch = int(time.time())
    for s in payload.get("STATUS", []):
        s["When"] = now_epoch

    devs = payload.get("DEVS", [])
    n = max(len(devs), 1)
    hf = live.hashrate_factor()

    for dev in devs:
        board_idx = dev.get("ASC", dev.get("ID", 0))
        td = live.temp_delta(board_idx)

        if "Temperature" in dev:
            dev["Temperature"] = round(dev["Temperature"] + td, 1)

        # MHS fields — note: these are MH/s, which is GH/s * 1000
        for key in ("MHS av", "MHS 5s"):
            if key in dev:
                dev[key] = round(dev[key] * hf, 1)
        if "MHS 15m" in dev:
            dev["MHS 15m"] = round(dev["MHS 15m"] * live.hashrate_factor_30m(), 1)

        dev["Accepted"]        = dev.get("Accepted", 0) + live.accepted_delta() // n
        dev["Rejected"]        = dev.get("Rejected", 0) + live.rejected_delta() // n
        dev["Hardware Errors"] = dev.get("Hardware Errors", 0) + live.hw_errors_delta() // n

    return payload


def _inject_luxos_power(
    payload: dict[str, Any], live: LiveMinerState, base_ghs: float
) -> dict[str, Any]:
    """Oscillate wattage and recalculate efficiency."""
    payload = copy.deepcopy(payload)
    now_epoch = int(time.time())
    for s in payload.get("STATUS", []):
        s["When"] = now_epoch

    power_list = payload.get("POWER", [])
    if not power_list:
        return payload
    pw = power_list[0]

    pf = live.power_factor()
    base_w = pw.get("Watts", 3245)
    current_w = round(base_w * pf)
    pw["Watts"] = current_w

    current_ghs = base_ghs * live.hashrate_factor()
    if current_ghs > 0:
        pw["Efficiency"] = round(current_w / (current_ghs / 1000.0), 2)

    return payload


def _inject_luxos_pools(
    payload: dict[str, Any], live: LiveMinerState
) -> dict[str, Any]:
    """Accumulate accepted/rejected/stale share counts."""
    payload = copy.deepcopy(payload)
    now_epoch = int(time.time())
    for s in payload.get("STATUS", []):
        s["When"] = now_epoch

    for p in payload.get("POOLS", []):
        new_acc = p.get("Accepted", 0) + live.accepted_delta()
        new_rej = p.get("Rejected", 0) + live.rejected_delta()
        p["Accepted"]             = new_acc
        p["Rejected"]             = new_rej
        p["Stale"]                = p.get("Stale", 0) + live.stale_delta()
        p["Difficulty Accepted"]  = round(
            p.get("Difficulty Accepted", 0) + live.difficulty_accepted_delta()
        )
        p["Last Share Time"]      = now_epoch
        # Slowly increment getwork count (~1/30s for a healthy pool)
        p["Getworks"] = p.get("Getworks", 0) + live.elapsed() // 30

        total = new_acc + new_rej
        if total > 0:
            p["Pool Rejected%"] = round(new_rej / total * 100, 3)

    return payload


# ---------------------------------------------------------------------------
# Auth state
# ---------------------------------------------------------------------------

class _AuthState:
    """Tracks the most-recently-issued auth token.

    Before any login request arrives, all traffic is allowed.
    After a login/unlock, every GET must carry the matching token.
    """

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._lock = threading.Lock()

    def issue(self, token: str) -> None:
        with self._lock:
            self._token = token

    def check(self, header_value: str) -> bool:
        with self._lock:
            if self._token is None:
                return True   # no login yet → allow
            return header_value == self._token


# ---------------------------------------------------------------------------
# Control-request dispatcher
# ---------------------------------------------------------------------------

def _dispatch_control(body: bytes, fan_state: FanState) -> tuple[int, dict[str, Any]]:
    try:
        req = json.loads(body.decode()) if body else {}
    except json.JSONDecodeError:
        return 400, {"error": "invalid JSON"}

    action = req.get("action", "")
    if action == "fan_dip":
        duration = float(req.get("duration_s", 8.0))
        fan_state.trigger_dip(duration)
        return 200, {"status": "ok", "action": "fan_dip", "duration_s": duration}
    if action == "fan_restore":
        fan_state.trigger_restore()
        return 200, {"status": "ok", "action": "fan_restore"}
    return 200, fan_state.status()


# ---------------------------------------------------------------------------
# HTTP server — Braiins and Vnish
# ---------------------------------------------------------------------------

_BRAIINS_GET = {
    "/api/v1/miner/details":       "miner_details",
    "/api/v1/cooling/state":       "cooling_state",
    "/api/v1/miner/stats":         "miner_stats",
    "/api/v1/miner/hw/hashboards": "hashboards",
    "/api/v1/miner/errors":        "miner_errors",
}
_BRAIINS_POST = {"/api/v1/auth/login": "auth_login"}

_VNISH_GET  = {
    "/api/v1/info":    "info",
    "/api/v1/summary": "summary",
    "/api/v1/status":  "status",
}
_VNISH_POST = {"/api/v1/unlock": "unlock"}

_HTTP_ROUTES: dict[str, dict[str, dict[str, str]]] = {
    "braiins": {"GET": _BRAIINS_GET, "POST": _BRAIINS_POST},
    "vnish":   {"GET": _VNISH_GET,   "POST": _VNISH_POST},
}

# Which header name each firmware's collector sends the token in
_AUTH_HEADER = {
    "braiins": "authorization",
    "vnish":   "Authorization",
}

# Which fixture key holds the token to issue on auth
_TOKEN_KEY = {"braiins": ("auth_login", "token"), "vnish": ("unlock", "token")}


def _make_http_handler(
    fixtures:   dict[str, Any],
    fan_state:  FanState,
    live_state: LiveMinerState,
    auth:       _AuthState,
) -> type[BaseHTTPRequestHandler]:
    routes      = _HTTP_ROUTES[FIRMWARE]
    auth_header = _AUTH_HEADER[FIRMWARE]
    tok_fixture, tok_field = _TOKEN_KEY[FIRMWARE]
    auth_path = "/api/v1/auth/login" if FIRMWARE == "braiins" else "/api/v1/unlock"

    # Build per-fixture-key injection closures.
    # Each lambda takes a raw fixture payload and returns an injected copy.
    if FIRMWARE == "braiins":
        _inject: dict[str, Callable[[dict], dict]] = {
            "miner_details": lambda p: _inject_braiins_miner_details(p, live_state),
            "cooling_state": lambda p: _inject_braiins_cooling_state(p, fan_state, live_state),
            "miner_stats":   lambda p: _inject_braiins_miner_stats(p, live_state),
            "hashboards":    lambda p: _inject_braiins_hashboards(p, live_state),
            # miner_errors: static — errors don't change on every poll
        }
    else:  # vnish
        _inject = {
            "summary": lambda p: _inject_vnish_summary(p, live_state),
            "status":  lambda p: _inject_vnish_status(p, fan_state, live_state),
            # info: static — identity fields don't change
        }

    class _Handler(BaseHTTPRequestHandler):
        server_version = "FakeMiner/1.0"
        sys_version    = ""

        def log_message(self, fmt, *args):
            logger.debug(fmt, *args)

        def _send_json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> bytes:
            n = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(n) if n else b""

        def do_GET(self):
            # /control is always public — no token required for monitoring
            if self.path == "/control":
                self._send_json(fan_state.status())
                return

            if not auth.check(self.headers.get(auth_header, "")):
                self._send_json({"error": "unauthorized"}, 401)
                return

            key = routes.get("GET", {}).get(self.path)
            if key is None:
                self._send_json({"error": "not found"}, 404)
                return

            payload = fixtures[key]
            injector = _inject.get(key)
            if injector:
                payload = injector(payload)
            self._send_json(payload)

        def do_POST(self):
            if self.path == "/control":
                status, resp = _dispatch_control(self._read_body(), fan_state)
                self._send_json(resp, status)
                return

            key = routes.get("POST", {}).get(self.path)
            if key is None:
                self._send_json({"error": "not found"}, 404)
                return

            self._read_body()  # consume request body (credentials not validated)

            if self.path == auth_path:
                auth.issue(fixtures[tok_fixture][tok_field])

            self._send_json(fixtures[key])

    return _Handler


# ---------------------------------------------------------------------------
# TCP server — LuxOS port 4028
# ---------------------------------------------------------------------------

_LUXOS_COMMANDS = {
    "version": "version",
    "config":  "config",
    "summary": "summary",
    "pools":   "pools",
    "power":   "power",
    "fans":    "fans",
    "temps":   "temps",
    "devs":    "devs",
    "events":  "events",
}

_LUXOS_ERROR = {
    "STATUS": [{"STATUS": "E", "Code": 14, "Msg": "Invalid command"}],
    "id": 1,
}


class _LuxOSHandler(socketserver.BaseRequestHandler):
    """One instance per accepted TCP connection."""

    fixtures:    dict[str, Any]                     = {}
    fan_state:   Optional[FanState]                 = None
    live_state:  Optional[LiveMinerState]           = None
    _inject_map: dict[str, Callable[[dict], dict]]  = {}

    def handle(self) -> None:
        raw = b""
        try:
            self.request.settimeout(5.0)
            while True:
                chunk = self.request.recv(4096)
                if not chunk:
                    break
                raw += chunk
                try:
                    json.loads(raw.decode("utf-8").rstrip("\x00"))
                    break
                except json.JSONDecodeError:
                    continue
        except socket.timeout:
            pass

        if not raw:
            return

        try:
            req = json.loads(raw.decode("utf-8").rstrip("\x00"))
        except json.JSONDecodeError:
            return

        cmd = req.get("command", "").lower().strip()
        key = _LUXOS_COMMANDS.get(cmd)
        if key:
            payload = self.fixtures.get(key, _LUXOS_ERROR)
            injector = self._inject_map.get(key)
            if injector:
                payload = injector(payload)
        else:
            payload = _LUXOS_ERROR

        try:
            self.request.sendall(json.dumps(payload).encode("utf-8"))
        except OSError:
            pass


def _make_luxos_handler(
    fixtures:   dict[str, Any],
    fan_state:  FanState,
    live_state: LiveMinerState,
) -> type[_LuxOSHandler]:
    # Extract base GHS for power efficiency recalculation
    base_ghs = (
        (fixtures.get("summary", {}).get("SUMMARY") or [{}])[0].get("GHS 5s", 145_000.0)
    )

    _inject: dict[str, Callable[[dict], dict]] = {
        "fans":    lambda p: _inject_luxos_fans(p, fan_state),
        "temps":   lambda p: _inject_luxos_temps(p, live_state),
        "summary": lambda p: _inject_luxos_summary(p, live_state),
        "devs":    lambda p: _inject_luxos_devs(p, live_state),
        "power":   lambda p: _inject_luxos_power(p, live_state, base_ghs),
        "pools":   lambda p: _inject_luxos_pools(p, live_state),
        # version, config, events: static
    }

    return type("Handler", (_LuxOSHandler,), {
        "fixtures":    fixtures,
        "fan_state":   fan_state,
        "live_state":  live_state,
        "_inject_map": _inject,
    })


class _ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# Fan-control HTTP server (used by all firmware, main HTTP port for
# Braiins/Vnish via /control; standalone on CONTROL_PORT for LuxOS)
# ---------------------------------------------------------------------------

def _make_control_handler(fan_state: FanState) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        server_version = "FakeMiner-Control/1.0"
        sys_version    = ""

        def log_message(self, fmt, *args):
            logger.debug(fmt, *args)

        def _send_json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> bytes:
            n = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(n) if n else b""

        def do_GET(self):
            self._send_json(fan_state.status())

        def do_POST(self):
            status, resp = _dispatch_control(self._read_body(), fan_state)
            self._send_json(resp, status)

    return _Handler


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if FIRMWARE not in ("braiins", "vnish", "luxos"):
        logger.error(
            "Unknown FIRMWARE=%r — must be braiins, vnish, or luxos", FIRMWARE
        )
        sys.exit(1)

    raw_fixtures = load_fixtures()
    fixtures     = personalize(raw_fixtures)

    # Build FanState from the un-personalised base fixture so every restart
    # begins from the same base RPMs regardless of jitter seed.
    if FIRMWARE == "braiins":
        base_rpms = {
            f["position"]: f["rpm"]
            for f in raw_fixtures.get("cooling_state", {}).get("fans", [])
        }
    elif FIRMWARE == "vnish":
        base_rpms = {
            f["id"]: f["rpm"]
            for f in raw_fixtures.get("status", {}).get("fans", [])
        }
    else:  # luxos
        base_rpms = {
            f["ID"]: f["RPM"]
            for f in raw_fixtures.get("fans", {}).get("FANS", [])
        }

    fan_state  = FanState(base_rpms, idx=MINER_INDEX)
    live_state = LiveMinerState(idx=MINER_INDEX)
    auth       = _AuthState()

    logger.info(
        "fake-%s miner #%d  live simulation active: "
        "hashrate ±%.0f%%, temp ±%.0f°C, power ±%.0f%%, "
        "shares accumulating at %.1f accepted/s",
        FIRMWARE, MINER_INDEX,
        live_state._HASH_AMP * 100,
        live_state._TEMP_AMP_C,
        live_state._POWER_AMP * 100,
        live_state._ACCEPTED_RATE,
    )

    if FIRMWARE in ("braiins", "vnish"):
        ThreadingHTTPServer.allow_reuse_address = True
        server = ThreadingHTTPServer(
            ("0.0.0.0", HTTP_PORT),
            _make_http_handler(fixtures, fan_state, live_state, auth),
        )
        logger.info(
            "fake-%s miner #%d  ready on HTTP port %d  (control: /control)",
            FIRMWARE, MINER_INDEX, HTTP_PORT,
        )
        server.serve_forever()

    else:  # luxos — TCP API + standalone control HTTP server
        tcp_server = _ThreadedTCPServer(
            ("0.0.0.0", LUXOS_PORT),
            _make_luxos_handler(fixtures, fan_state, live_state),
        )
        t_tcp = threading.Thread(target=tcp_server.serve_forever, daemon=True)
        t_tcp.start()
        logger.info(
            "fake-luxos miner #%d  TCP API ready on port %d",
            MINER_INDEX, LUXOS_PORT,
        )

        # Standalone HTTP control server for LuxOS containers
        ThreadingHTTPServer.allow_reuse_address = True
        ctrl_server = ThreadingHTTPServer(
            ("0.0.0.0", CONTROL_PORT),
            _make_control_handler(fan_state),
        )
        logger.info(
            "fake-luxos control server ready on HTTP port %d  "
            "(GET / → status, POST / → {\"action\":\"fan_dip\"})",
            CONTROL_PORT,
        )
        ctrl_server.serve_forever()


if __name__ == "__main__":
    main()
