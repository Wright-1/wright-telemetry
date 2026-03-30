"""Auto-update via GitHub Releases. Polls on a background thread.

Never raises — a failed update check must not prevent the collector from running.

Flow:
  1. Fetch latest release from GitHub API
  2. Compare tag version to running __version__
  3. If newer, download the platform-appropriate asset
  4. Replace the running binary and restart the process
  5. Sleep for update_check_interval seconds and repeat
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

GITHUB_REPO = "Wright-1/wright-telemetry"
_RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_TIMEOUT = 15  # seconds for network requests

# Maps sys.platform to the GitHub Release asset name for that platform
_ASSET_NAMES: dict[str, str] = {
    "linux": "wright-telemetry",
    "darwin": "wright-telemetry-macos.zip",
    "win32": "wright-telemetry.exe",
}


_DEFAULT_INTERVAL = 3600  # 1 hour


def check_for_update(cfg: dict) -> None:
    """Start a background thread that polls for updates indefinitely."""
    if cfg.get("disable_auto_update", False):
        logger.debug("Auto-update check disabled by config")
        return
    interval = int(cfg.get("update_check_interval", _DEFAULT_INTERVAL))
    threading.Thread(target=_update_loop, args=(interval,), daemon=True).start()


def _update_loop(interval: int) -> None:
    while True:
        try:
            _perform_update_check()
        except Exception as exc:
            logger.warning("Update check failed (non-fatal): %s", exc)
        time.sleep(interval)


def _perform_update_check() -> None:
    # Only applies to frozen PyInstaller binaries; skip in dev/source installs
    if not getattr(sys, "frozen", False):
        logger.debug("Running from source — skipping update check")
        return

    from wright_telemetry import __version__

    release = _fetch_latest_release()
    if release is None:
        return

    latest_version = release["tag_name"].lstrip("v")
    if not _is_newer(latest_version, __version__):
        logger.info("wright-telemetry is up to date (v%s)", __version__)
        return

    logger.info(
        "Update available: v%s -> v%s. Downloading...",
        __version__,
        latest_version,
    )

    asset = _find_asset(release["assets"])
    if asset is None:
        logger.warning(
            "No release asset found for platform %s — skipping update", sys.platform
        )
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        download_path = Path(tmpdir) / asset["name"]
        _download(asset["browser_download_url"], download_path)
        new_binary = _extract_binary(download_path, Path(tmpdir))
        if new_binary is None:
            logger.warning("Could not extract binary from downloaded asset")
            return
        _replace_and_restart(new_binary)


def _fetch_latest_release() -> dict | None:
    try:
        resp = requests.get(_RELEASES_URL, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.warning("Could not reach GitHub releases API: %s", exc)
        return None


def _is_newer(latest: str, current: str) -> bool:
    """Return True if latest version tuple is greater than current."""
    try:
        return tuple(int(x) for x in latest.split(".")) > tuple(
            int(x) for x in current.split(".")
        )
    except ValueError:
        return False


def _find_asset(assets: list[dict]) -> dict | None:
    target = _ASSET_NAMES.get(sys.platform)
    if target is None:
        return None
    for asset in assets:
        if asset["name"] == target:
            return asset
    return None


def _download(url: str, dest: Path) -> None:
    resp = requests.get(url, stream=True, timeout=_TIMEOUT)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)


def _extract_binary(asset_path: Path, workdir: Path) -> Path | None:
    """Extract the binary from the asset archive (or return path as-is for bare binaries)."""
    name = asset_path.name

    if name.endswith(".tar.gz"):
        with tarfile.open(asset_path) as tf:
            tf.extractall(workdir)
        binary = workdir / "wright-telemetry"
        return binary if binary.exists() else None

    if name.endswith(".zip"):
        with zipfile.ZipFile(asset_path) as zf:
            zf.extractall(workdir)
        # macOS zip contains bare binary; Windows zip contains the .exe
        for candidate in ("wright-telemetry", "wright-telemetry.exe"):
            binary = workdir / candidate
            if binary.exists():
                return binary
        return None

    # Linux: bare binary with no extension
    return asset_path


def _replace_and_restart(new_binary: Path) -> None:
    current = Path(sys.executable)

    if sys.platform == "win32":
        _replace_and_restart_windows(new_binary, current)
    else:
        _replace_and_restart_unix(new_binary, current)


def _replace_and_restart_unix(new_binary: Path, current: Path) -> None:
    new_binary.chmod(0o755)
    # On Unix we can overwrite the file while the process is running;
    # the running inode stays alive until execv replaces the process image.
    shutil.copy2(new_binary, current)
    logger.info("Update applied. Restarting...")
    os.execv(str(current), sys.argv)


def _replace_and_restart_windows(new_binary: Path, current: Path) -> None:
    # Windows won't let us overwrite a running executable.
    # Write the new binary alongside the current one, then launch a PowerShell
    # one-liner that waits for this process to exit, swaps the files, and restarts.
    staged = current.with_name(current.stem + "-update" + current.suffix)
    shutil.copy2(new_binary, staged)

    script = (
        f"Start-Sleep -Seconds 2; "
        f"Move-Item -Force '{staged}' '{current}'; "
        f"Start-Process '{current}'"
    )
    subprocess.Popen(
        ["powershell", "-NonInteractive", "-Command", script],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    logger.info("Update staged. Restarting via PowerShell helper...")
    sys.exit(0)
