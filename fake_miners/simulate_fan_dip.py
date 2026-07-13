#!/usr/bin/env python3
"""Trigger fan-dip events on the fake miner fleet to simulate power getting
cut to a single fan — RPM drops to 0 (well below the 1k RPM detection floor)
for a duration, then ramps back up to speed. Targets exactly one fan
position by default, since that's what the real detection logic requires
(an isolated single-fan drop; more than one fan dropping at once is treated
as ambiguous and ignored) and what a real switch flip looks like.

Run this alongside the wright-telemetry GUI/agent (which should already be
in "fan detection" mode) to exercise the detection logic without touching
real hardware.

Examples
--------
    # Dip a random fan on one miner for the default 8s
    python simulate_fan_dip.py braiins-a

    # Dip a specific fan position for 15s
    python simulate_fan_dip.py vnish-b --fan 2 --duration 15

    # Cancel an in-progress dip early
    python simulate_fan_dip.py luxos-c --restore

    # Whole-unit power loss (all fans at once) — should be ignored by
    # detection, useful for testing the ambiguous-multi-fan suppression
    python simulate_fan_dip.py braiins-a --all-fans

    # Keep dipping random fans on random miners every 20s until Ctrl+C
    python simulate_fan_dip.py random --loop --interval 20
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request

# host/control-port for every fake miner in fake_miners/docker-compose.yml.
# Braiins/Vnish/Bitmain expose /control on their main HTTP port (80);
# LuxOS is TCP-only for the miner protocol, so its /control lives on the
# separate CONTROL_PORT (8080).
MINERS: dict[str, tuple[str, int]] = {
    "braiins-a": ("172.28.0.10", 80),
    "vnish-a": ("172.28.0.20", 80),
    "luxos-a": ("172.28.0.30", 8080),
    "bitmain-a": ("172.28.0.40", 80),
    "braiins-b": ("172.28.1.10", 80),
    "vnish-b": ("172.28.1.20", 80),
    "luxos-b": ("172.28.1.30", 8080),
    "bitmain-b": ("172.28.1.40", 80),
    "braiins-c": ("172.28.2.10", 80),
    "vnish-c": ("172.28.2.20", 80),
    "luxos-c": ("172.28.2.30", 8080),
    "bitmain-c": ("172.28.2.40", 80),
}


def send_control(host: str, port: int, body: dict) -> dict:
    req = urllib.request.Request(
        f"http://{host}:{port}/control",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def get_status(host: str, port: int) -> dict:
    with urllib.request.urlopen(f"http://{host}:{port}/control", timeout=5) as resp:
        return json.loads(resp.read().decode())


def _poll_rpm(host: str, port: int, fan_position: int) -> int | None:
    try:
        status = get_status(host, port)
        return status["fans"][str(fan_position)]["rpm"]
    except (urllib.error.URLError, TimeoutError, KeyError):
        return None


def watch_rpm(
    host: str,
    port: int,
    fan_position: int,
    duration: float,
    trigger: "Callable[[], dict]",
    pre_seconds: float = 3.0,
    poll_interval: float = 0.5,
) -> None:
    """Poll /control for a fan's RPM before, during, and after the dip so the
    graph shows the full signature (normal -> dip -> recovery), then render
    it as a terminal bar graph. `trigger` fires the dip itself, at t=0."""
    samples: list[tuple[float, int]] = []

    # baseline: a few seconds of normal RPM before the dip is even triggered
    pre_start = time.time()
    while time.time() - pre_start < pre_seconds:
        rpm = _poll_rpm(host, port, fan_position)
        if rpm is not None:
            samples.append((time.time() - pre_start - pre_seconds, rpm))
        time.sleep(poll_interval)

    t0 = time.time()
    trigger()

    # keep polling a couple seconds past the dip to capture the ramp-back-up
    end = t0 + duration + 3.0
    while time.time() < end:
        rpm = _poll_rpm(host, port, fan_position)
        if rpm is not None:
            samples.append((time.time() - t0, rpm))
        time.sleep(poll_interval)

    if not samples:
        print("(no RPM samples collected)")
        return

    print(render_graph(samples, fan_position))


_ROWS = 12


def render_graph(samples: list[tuple[float, int]], fan_position: int) -> str:
    rpms = [rpm for _, rpm in samples]
    hi = max(rpms) or 1

    lines = [f"fan #{fan_position} RPM over {samples[-1][0] - samples[0][0]:.1f}s (peak {hi} RPM)"]
    for row in range(_ROWS, 0, -1):
        threshold = hi * row / _ROWS
        line = "".join("#" if rpm >= threshold else " " for rpm in rpms)
        lines.append(f"{int(threshold):>5} |{line}")
    zero_line = "".join("_" if rpm == 0 else " " for rpm in rpms)
    lines.append(f"{0:>5} |{zero_line}")
    lines.append(f"{'':>5} +" + "-" * len(rpms))
    lines.append(f"{'':>6}" + "".join(f"{s[0]:.0f}".ljust(1) if i % max(1, len(samples) // 10) == 0 else " " for i, s in enumerate(samples)))
    return "\n".join(lines)


# Every fake miner fixture (braiins/vnish/luxos/bitmain) has 4 fan positions.
FAN_POSITIONS = [0, 1, 2, 3]


def dip(name: str, duration: float, restore: bool, fan_position: int | None, graph: bool) -> None:
    host, port = MINERS[name]
    action = "fan_restore" if restore else "fan_dip"
    body: dict = {"action": action}
    if not restore:
        body["duration_s"] = duration
    if fan_position is not None:
        body["fan_position"] = fan_position
    target = f"fan #{fan_position}" if fan_position is not None else "all fans"

    def trigger() -> dict:
        try:
            resp = send_control(host, port, body)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"[{name}] FAILED to reach {host}:{port}/control: {exc}", file=sys.stderr)
            return {}
        if restore:
            print(f"[{name}] {target} restored: {resp}")
        else:
            print(f"[{name}] {target} dip triggered for {duration:.0f}s: {resp}")
        return resp

    if restore or fan_position is None or not graph:
        trigger()
        return

    watch_rpm(host, port, fan_position, duration, trigger=trigger)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "miner",
        choices=[*MINERS.keys(), "random"],
        help="Fake miner to target, or 'random' to pick one each time",
    )
    parser.add_argument("--fan", type=int, choices=FAN_POSITIONS, help="Fan position to dip (default: random single fan)")
    parser.add_argument("--all-fans", action="store_true", help="Dip every fan on the miner at once (whole-unit power loss — detection should ignore this)")
    parser.add_argument("--duration", type=float, default=8.0, help="Dip duration in seconds (default: 8)")
    parser.add_argument("--restore", action="store_true", help="Cancel an in-progress dip early instead of starting one")
    parser.add_argument("--loop", action="store_true", help="Keep triggering dips until Ctrl+C")
    parser.add_argument("--interval", type=float, default=20.0, help="Seconds between dips in --loop mode (default: 20)")
    parser.add_argument("--no-graph", action="store_true", help="Skip printing the RPM-over-time graph after the dip")
    args = parser.parse_args()

    if args.all_fans and args.fan is not None:
        parser.error("--fan and --all-fans are mutually exclusive")

    graph = not args.no_graph and not args.loop

    def pick_miner() -> str:
        return random.choice(list(MINERS.keys())) if args.miner == "random" else args.miner

    def pick_fan() -> int | None:
        if args.all_fans:
            return None
        return args.fan if args.fan is not None else random.choice(FAN_POSITIONS)

    if not args.loop:
        dip(pick_miner(), args.duration, args.restore, pick_fan(), graph)
        return

    print(f"Looping — a dip every {args.interval:.0f}s (duration {args.duration:.0f}s each). Ctrl+C to stop.")
    try:
        while True:
            dip(pick_miner(), args.duration, args.restore, pick_fan(), graph)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
