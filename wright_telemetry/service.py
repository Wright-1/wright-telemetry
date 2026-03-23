"""OS service install / uninstall for background operation.

Registers the collector as a service that starts on boot and restarts
on failure.  Supports:
    - Linux:   systemd user service
    - macOS:   launchd LaunchAgent
    - Windows: Task Scheduler
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

_SERVICE_NAME = "wright-telemetry"
_LAUNCHD_LABEL = "com.wrightfan.telemetry"


def _get_executable() -> str:
    """Return the path to the running executable (PyInstaller binary or python)."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return f"{sys.executable} -m wright_telemetry"


# ------------------------------------------------------------------
# Linux (systemd)
# ------------------------------------------------------------------

def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{_SERVICE_NAME}.service"


def _install_systemd() -> None:
    exe = _get_executable()
    unit = textwrap.dedent(f"""\
        [Unit]
        Description=Wright Telemetry Collector
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=simple
        ExecStart={exe}
        Restart=always
        RestartSec=10
        Environment=WRIGHT_LOKI_AUTH=%E{_SERVICE_NAME}/loki_auth

        [Install]
        WantedBy=default.target
    """)

    unit_path = _systemd_unit_path()
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(unit)

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", _SERVICE_NAME], check=True)

    # Enable lingering so the service runs without an active login session
    try:
        subprocess.run(["loginctl", "enable-linger"], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("  Note: could not enable linger. The service may stop when you log out.")

    print(f"  Installed systemd service: {unit_path}")
    print(f"  Status: systemctl --user status {_SERVICE_NAME}")


def _uninstall_systemd() -> None:
    subprocess.run(["systemctl", "--user", "stop", _SERVICE_NAME], check=False)
    subprocess.run(["systemctl", "--user", "disable", _SERVICE_NAME], check=False)
    unit_path = _systemd_unit_path()
    if unit_path.exists():
        unit_path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    print(f"  Removed systemd service.")


# ------------------------------------------------------------------
# macOS (launchd)
# ------------------------------------------------------------------

def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"


def _install_launchd() -> None:
    exe = _get_executable()
    program_args = exe.split()

    args_xml = "\n        ".join(f"<string>{a}</string>" for a in program_args)

    plist = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>{_LAUNCHD_LABEL}</string>
            <key>ProgramArguments</key>
            <array>
                {args_xml}
            </array>
            <key>RunAtLoad</key>
            <true/>
            <key>KeepAlive</key>
            <true/>
            <key>StandardOutPath</key>
            <string>{Path.home() / ".wright-telemetry" / "stdout.log"}</string>
            <key>StandardErrorPath</key>
            <string>{Path.home() / ".wright-telemetry" / "stderr.log"}</string>
        </dict>
        </plist>
    """)

    plist_path = _launchd_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist)

    subprocess.run(["launchctl", "load", str(plist_path)], check=True)
    print(f"  Installed launchd agent: {plist_path}")
    print(f"  Status: launchctl list | grep {_LAUNCHD_LABEL}")


def _uninstall_launchd() -> None:
    plist_path = _launchd_plist_path()
    if plist_path.exists():
        subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
        plist_path.unlink()
    print("  Removed launchd agent.")


# ------------------------------------------------------------------
# Windows (Task Scheduler)
# ------------------------------------------------------------------

def _install_windows_task() -> None:
    exe = _get_executable()
    task_name = f"\\WrightFan\\{_SERVICE_NAME}"

    # Create the task
    cmd = [
        "schtasks", "/Create",
        "/TN", task_name,
        "/TR", exe,
        "/SC", "ONSTART",
        "/RL", "HIGHEST",
        "/F",  # force overwrite
    ]
    subprocess.run(cmd, check=True)

    # Configure restart on failure via XML import would be ideal, but
    # schtasks /Create covers the basics. For restart-on-failure we use
    # the inner crash recovery loop in scheduler.py.

    print(f"  Installed Windows scheduled task: {task_name}")
    print(f"  Status: schtasks /Query /TN \"{task_name}\"")


def _uninstall_windows_task() -> None:
    task_name = f"\\WrightFan\\{_SERVICE_NAME}"
    subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], check=False)
    print("  Removed Windows scheduled task.")


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def install_service() -> None:
    """Register the collector as a background service on the current OS."""
    system = platform.system()
    print(f"\n  Installing background service ({system})...")

    if system == "Linux":
        _install_systemd()
    elif system == "Darwin":
        _install_launchd()
    elif system == "Windows":
        _install_windows_task()
    else:
        print(f"  Unsupported platform: {system}. You'll need to set up auto-start manually.")


def uninstall_service() -> None:
    """Remove the background service registration."""
    system = platform.system()
    print(f"\n  Removing background service ({system})...")

    if system == "Linux":
        _uninstall_systemd()
    elif system == "Darwin":
        _uninstall_launchd()
    elif system == "Windows":
        _uninstall_windows_task()
    else:
        print(f"  Unsupported platform: {system}.")
