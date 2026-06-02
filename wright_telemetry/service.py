"""OS service install / uninstall for background operation.

Registers the collector as a service that starts on boot and restarts
on failure.  Supports:
    - Linux:   systemd user service
    - macOS:   launchd LaunchAgent
    - Windows: Task Scheduler
"""

from __future__ import annotations

import getpass
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
        Restart=on-failure
        RestartSec=15
        # Disable the default rate-limit so systemd never permanently gives up
        StartLimitIntervalSec=0
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
        subprocess.run(["loginctl", "enable-linger", getpass.getuser()], check=True)
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

    log_dir = Path.home() / ".wright-telemetry"
    log_dir.mkdir(parents=True, exist_ok=True)

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
            <dict>
                <!-- Restart on crash, but NOT on a clean sys.exit(0) -->
                <!-- This lets --uninstall and graceful shutdowns stay down -->
                <key>Crashed</key>
                <true/>
                <key>SuccessfulExit</key>
                <false/>
            </dict>
            <key>ThrottleInterval</key>
            <integer>30</integer>
            <key>ProcessType</key>
            <string>Background</string>
            <key>StandardOutPath</key>
            <string>{log_dir / "stdout.log"}</string>
            <key>StandardErrorPath</key>
            <string>{log_dir / "stderr.log"}</string>
        </dict>
        </plist>
    """)

    plist_path = _launchd_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist)

    uid = os.getuid()
    domain_target = f"gui/{uid}"

    # Remove stale registration if present
    subprocess.run(
        ["launchctl", "bootout", f"{domain_target}/{_LAUNCHD_LABEL}"],
        check=False, capture_output=True,
    )
    try:
        subprocess.run(
            ["launchctl", "bootstrap", domain_target, str(plist_path)],
            check=True,
        )
    except subprocess.CalledProcessError:
        # Fallback for macOS < 10.10
        subprocess.run(["launchctl", "load", str(plist_path)], check=True)

    print(f"  Installed launchd agent: {plist_path}")
    print(f"  Status: launchctl list | grep {_LAUNCHD_LABEL}")


def _uninstall_launchd() -> None:
    plist_path = _launchd_plist_path()
    uid = os.getuid()
    domain_target = f"gui/{uid}"

    try:
        subprocess.run(
            ["launchctl", "bootout", f"{domain_target}/{_LAUNCHD_LABEL}"],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError:
        if plist_path.exists():
            subprocess.run(["launchctl", "unload", str(plist_path)], check=False)

    if plist_path.exists():
        plist_path.unlink()
    print("  Removed launchd agent.")


# ------------------------------------------------------------------
# Windows (Task Scheduler + PowerShell watchdog)
# ------------------------------------------------------------------

_WATCHDOG_SCRIPT_NAME = "wright-telemetry-watchdog.ps1"


def _watchdog_script_path(exe: str) -> Path:
    """Place the watchdog script alongside the binary."""
    return Path(exe).parent / _WATCHDOG_SCRIPT_NAME


def _write_watchdog_script(exe: str) -> Path:
    """Write the PowerShell crash-restart loop and return its path.

    The loop restarts the binary on any non-zero exit code, but stops
    cleanly on exit code 0 (which wright-telemetry uses for --uninstall
    and graceful shutdowns).  A 15-second sleep between restarts prevents
    tight CPU-burning loops on repeated crashes.
    """
    script = textwrap.dedent(f"""\
        # Wright Telemetry watchdog — do not edit manually.
        # Restarts the agent on crash; stops on clean exit (code 0).
        $binary = "{exe}"
        while ($true) {{
            $proc = Start-Process -FilePath $binary -PassThru -Wait -WindowStyle Hidden
            if ($proc.ExitCode -eq 0) {{ exit 0 }}
            Start-Sleep -Seconds 15
        }}
    """)
    script_path = _watchdog_script_path(exe)
    script_path.write_text(script, encoding="utf-8")
    return script_path


def _install_windows_task() -> None:
    exe = _get_executable()
    script_path = _write_watchdog_script(exe)
    task_name = f"\\WrightFan\\{_SERVICE_NAME}"

    # Run the PowerShell watchdog (which in turn runs the binary).
    # -WindowStyle Hidden keeps it off the taskbar.
    # -ExecutionPolicy Bypass avoids policy blocks on the watchdog script.
    tr = (
        f'powershell.exe -NonInteractive -WindowStyle Hidden '
        f'-ExecutionPolicy Bypass -File "{script_path}"'
    )
    cmd = [
        "schtasks", "/Create",
        "/TN", task_name,
        "/TR", tr,
        "/SC", "ONLOGON",
        "/F",  # force overwrite
        "/RL", "HIGHEST",  # run with highest available privileges
    ]
    subprocess.run(cmd, check=True)

    print(f"  Installed Windows scheduled task: {task_name}")
    print(f"  Watchdog script: {script_path}")
    print(f"  Status: schtasks /Query /TN \"{task_name}\"")


def _uninstall_windows_task() -> None:
    task_name = f"\\WrightFan\\{_SERVICE_NAME}"
    subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], check=False)

    # Also clean up the watchdog script if it exists
    exe = _get_executable()
    script_path = _watchdog_script_path(exe)
    if script_path.exists():
        try:
            script_path.unlink()
        except OSError:
            pass

    print("  Removed Windows scheduled task and watchdog script.")


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
