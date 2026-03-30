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

import hashlib
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
_CONNECT_TIMEOUT = 15  # seconds to establish connection
_READ_TIMEOUT = 60    # seconds per socket read (important for large binaries)

# Maps sys.platform to the GitHub Release asset name for that platform
_ASSET_NAMES: dict[str, str] = {
    "linux": "wright-telemetry",
    "darwin": "wright-telemetry-macos.zip",
    "win32": "wright-telemetry.exe",
}


_DEFAULT_INTERVAL = 60  # 1 minute


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

    checksum_asset = _find_checksum_asset(release["assets"], asset["name"])
    if checksum_asset is None:
        logger.warning("No checksum asset found for %s — skipping update", asset["name"])
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        download_path = tmppath / asset["name"]
        checksum_path = tmppath / checksum_asset["name"]
        _download(asset["browser_download_url"], download_path)
        _download(checksum_asset["browser_download_url"], checksum_path)
        _verify_checksum(download_path, checksum_path)
        new_binary = _extract_binary(download_path, tmppath)
        if new_binary is None:
            logger.warning("Could not extract binary from downloaded asset")
            return
        _replace_and_restart(new_binary)


def _fetch_latest_release() -> dict | None:
    try:
        resp = requests.get(_RELEASES_URL, timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT))
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


def _find_checksum_asset(assets: list[dict], asset_name: str) -> dict | None:
    target = asset_name + ".sha256"
    for asset in assets:
        if asset["name"] == target:
            return asset
    return None


def _verify_checksum(download_path: Path, checksum_path: Path) -> None:
    """Raise ValueError if the SHA256 of download_path doesn't match checksum_path."""
    # .sha256 files use sha256sum format: "<hex>  <filename>"
    expected_hex = checksum_path.read_text().split()[0].lower()
    actual_hex = hashlib.sha256(download_path.read_bytes()).hexdigest()
    if actual_hex != expected_hex:
        raise ValueError(
            f"Checksum mismatch for {download_path.name}: "
            f"expected {expected_hex}, got {actual_hex}"
        )


def _download(url: str, dest: Path) -> None:
    resp = requests.get(url, stream=True, timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT))
    resp.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)


def _safe_extractall_tar(tf: tarfile.TarFile, workdir: Path) -> None:
    resolved_workdir = workdir.resolve()
    for member in tf.getmembers():
        member_path = (workdir / member.name).resolve()
        if not member_path.is_relative_to(resolved_workdir):
            raise ValueError(f"Path traversal detected in archive: {member.name}")
    tf.extractall(workdir)


def _safe_extractall_zip(zf: zipfile.ZipFile, workdir: Path) -> None:
    resolved_workdir = workdir.resolve()
    for name in zf.namelist():
        member_path = (workdir / name).resolve()
        if not member_path.is_relative_to(resolved_workdir):
            raise ValueError(f"Path traversal detected in archive: {name}")
    zf.extractall(workdir)


def _extract_binary(asset_path: Path, workdir: Path) -> Path | None:
    """Extract the binary from the asset archive (or return path as-is for bare binaries)."""
    name = asset_path.name

    if name.endswith(".tar.gz"):
        with tarfile.open(asset_path) as tf:
            _safe_extractall_tar(tf, workdir)
        binary = workdir / "wright-telemetry"
        return binary if binary.exists() else None

    if name.endswith(".zip"):
        with zipfile.ZipFile(asset_path) as zf:
            _safe_extractall_zip(zf, workdir)
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
    # Stage next to the target so os.rename is atomic (same filesystem).
    # The running inode stays alive until execv replaces the process image.
    staged = current.with_suffix(".new")
    shutil.copy2(new_binary, staged)
    staged.chmod(0o755)
    os.rename(staged, current)
    logger.info("Update applied. Restarting...")
    os.execv(str(current), sys.argv)


def _replace_and_restart_windows(new_binary: Path, current: Path) -> None:
    # Windows won't let us overwrite a running executable.
    # Write the new binary alongside the current one, then launch a PowerShell
    # one-liner that waits for this process to exit, swaps the files, and restarts.
    staged = current.with_name(current.stem + "-update" + current.suffix)
    shutil.copy2(new_binary, staged)

    pid = os.getpid()
    script = (
        f"$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
        f"if ($p) {{ Wait-Process -Id {pid} -Timeout 30 -ErrorAction SilentlyContinue }}; "
        f"if (!(Move-Item -Force '{staged}' '{current}' -PassThru)) {{ exit 1 }}; "
        f"Start-Process '{current}'"
    )
    subprocess.Popen(
        ["powershell", "-NonInteractive", "-Command", script],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    logger.info("Update staged. Restarting via PowerShell helper...")
    sys.exit(0)
