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
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GITHUB_REPO = "Wright-1/wright-telemetry"
_RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_CONNECT_TIMEOUT = 15  # seconds to establish connection
_READ_TIMEOUT = 60    # seconds per socket read (important for large binaries)

_MAX_BACKOFF = 3600  # seconds

# Release asset filenames to try, in order, for each host OS only (never cross-platform).
_LEGACY_LINUX_ASSET = "wright-telemetry"  # older releases before linux used a distinct name


def _running_os() -> Optional[str]:
    """Return 'linux', 'darwin', or 'win32' for supported frozen builds, else None."""
    plat = sys.platform
    if plat.startswith("linux"):
        return "linux"
    if plat == "darwin":
        return "darwin"
    if plat == "win32":
        return "win32"
    return None


def _release_asset_candidates(os_name: str) -> tuple[str, ...]:
    if os_name == "linux":
        return ("wright-telemetry-linux", _LEGACY_LINUX_ASSET)
    if os_name == "darwin":
        return ("wright-telemetry-macos.zip",)
    if os_name == "win32":
        return ("wright-telemetry.exe",)
    return ()


_DEFAULT_INTERVAL = 60  # 1 minute


def check_for_update(cfg: dict) -> None:
    """Start a background thread that polls for updates indefinitely."""
    if cfg.get("disable_auto_update", False):
        logger.debug("Auto-update check disabled by config")
        return
    interval = int(cfg.get("update_check_interval", _DEFAULT_INTERVAL))
    threading.Thread(target=_update_loop, args=(interval,), daemon=True).start()


def _update_loop(interval: int) -> None:
    backoff = float(interval)
    while True:
        try:
            ok, rate_limit_sleep = _perform_update_check()
        except Exception as exc:
            logger.warning("Update check failed (non-fatal): %s", exc)
            ok = False
            rate_limit_sleep = None

        if ok:
            backoff = float(interval)
            time.sleep(interval)
            continue

        if rate_limit_sleep is not None:
            wait = max(rate_limit_sleep, backoff)
            logger.info("Next update check in %.0fs (rate limit or backoff)", wait)
            time.sleep(wait)
            backoff = min(max(backoff * 2, interval), _MAX_BACKOFF)
        else:
            wait = backoff
            logger.info("Next update check in %.0fs (backoff after error)", wait)
            time.sleep(wait)
            backoff = min(backoff * 2, _MAX_BACKOFF)


def _github_session_headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token.strip()}"}
    return {}


def _perform_update_check() -> tuple[bool, Optional[float]]:
    """Return (True, None) if the check finished without a recoverable API failure.

    On failure, returns (False, None) for generic errors or (False, seconds) when
    GitHub rate-limited (caller should sleep at least that long).
    """
    # Only applies to frozen PyInstaller binaries; skip in dev/source installs
    if not getattr(sys, "frozen", False):
        logger.debug("Running from source — skipping update check")
        return True, None

    from wright_telemetry import __version__

    release, rate_sleep = _fetch_latest_release()
    if release is None:
        return False, rate_sleep

    latest_version = release["tag_name"].lstrip("v")
    if not _is_newer(latest_version, __version__):
        logger.info("wright-telemetry is up to date (v%s)", __version__)
        return True, None

    logger.info(
        "Update available: v%s -> v%s. Downloading...",
        __version__,
        latest_version,
    )

    asset = _find_asset_for_os(release["assets"])
    if asset is None:
        os_name = _running_os() or sys.platform
        logger.warning(
            "No release asset found for this OS (%s) — skipping update",
            os_name,
        )
        return True, None

    checksum_asset = _find_checksum_asset(release["assets"], asset["name"])
    if checksum_asset is None:
        logger.warning("No checksum asset found for %s — skipping update", asset["name"])
        return True, None

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
            return True, None
        _replace_and_restart(new_binary)


def _fetch_latest_release() -> tuple[Optional[dict], Optional[float]]:
    """Return (release_json, rate_limit_retry_after_seconds).

    On success: (dict, None). On failure: (None, None) or (None, seconds) for 403.
    """
    headers = _github_session_headers()
    try:
        resp = requests.get(
            _RELEASES_URL,
            headers=headers,
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
        )
    except requests.RequestException as exc:
        logger.warning("Could not reach GitHub releases API: %s", exc)
        return None, None

    if resp.status_code == 403:
        reset = resp.headers.get("X-RateLimit-Reset")
        retry_after = resp.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            sleep_s = float(retry_after) + 2.0
        elif reset and reset.isdigit():
            sleep_s = max(0.0, float(int(reset) - time.time())) + 5.0
        else:
            sleep_s = float(_MAX_BACKOFF)
        logger.warning(
            "GitHub releases API returned 403 (rate limit or forbidden); retry in ~%.0fs",
            sleep_s,
        )
        return None, sleep_s

    try:
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Could not reach GitHub releases API: %s", exc)
        return None, None

    return resp.json(), None


def _is_newer(latest: str, current: str) -> bool:
    """Return True if latest version tuple is greater than current."""
    try:
        return tuple(int(x) for x in latest.split(".")) > tuple(
            int(x) for x in current.split(".")
        )
    except ValueError:
        return False


def _find_asset_for_os(assets: list[dict]) -> dict | None:
    os_name = _running_os()
    if os_name is None:
        logger.warning("Auto-update not supported on platform %s", sys.platform)
        return None
    names = _release_asset_candidates(os_name)
    by_name = {a["name"]: a for a in assets}
    for name in names:
        if name in by_name:
            return by_name[name]
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

    # Linux: bare binary (e.g. wright-telemetry-linux or legacy wright-telemetry)
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
