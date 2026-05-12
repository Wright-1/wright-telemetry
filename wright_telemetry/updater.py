"""Auto-update via the Wright API. Polls on a background thread.

Never raises — a failed update check must not prevent the collector from running.

Flow:
  1. Call GET /api/agent/updates/check on the Wright API
  2. If an update is available, download the binary from the returned URL
  3. Verify SHA256 checksum
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
import urllib3

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 15  # seconds to establish connection
_READ_TIMEOUT = 60     # seconds per socket read (important for large binaries)
_MAX_BACKOFF = 3600    # seconds
_DEFAULT_INTERVAL = 60  # 1 minute


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


def _update_check_url(api_url: str) -> str:
    """Build the update check URL from the configured Wright API base.

    Config typically stores ``https://api.wrightone.io/api`` (with /api mount)
    or just ``https://api.wrightone.io`` (bare host).  The Fastify endpoint
    lives at ``/api/agent/updates/check``.
    """
    base = (api_url or "").strip().rstrip("/")
    if base.endswith("/api"):
        return f"{base}/agent/updates/check"
    return f"{base}/api/agent/updates/check"


def check_for_update(cfg: dict) -> None:
    """Start a background thread that polls for updates indefinitely."""
    if cfg.get("disable_auto_update", False):
        logger.debug("Auto-update check disabled by config")
        return
    interval = int(cfg.get("update_check_interval", _DEFAULT_INTERVAL))
    api_url = cfg.get("wright_api_url", "")
    threading.Thread(
        target=_update_loop, args=(interval, api_url), daemon=True
    ).start()


def _update_loop(interval: int, api_url: str) -> None:
    backoff = float(interval)
    while True:
        try:
            ok = _perform_update_check(api_url)
        except Exception as exc:
            logger.warning("Update check failed (non-fatal): %s", exc)
            ok = False

        if ok:
            backoff = float(interval)
            time.sleep(interval)
            continue

        wait = backoff
        logger.info("Next update check in %.0fs (backoff after error)", wait)
        time.sleep(wait)
        backoff = min(backoff * 2, _MAX_BACKOFF)


def _perform_update_check(api_url: str) -> bool:
    """Return True if the check completed cleanly (up to date or updated)."""
    # Only applies to frozen PyInstaller binaries; skip in dev/source installs
    if not getattr(sys, "frozen", False):
        logger.debug("Running from source — skipping update check")
        return True

    from wright_telemetry import __version__

    os_name = _running_os()
    if os_name is None:
        logger.warning("Auto-update not supported on platform %s", sys.platform)
        return True

    url = _update_check_url(api_url)
    try:
        # Suppress TLS warnings — matches existing WrightAPIClient pattern
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        resp = requests.get(
            url,
            params={"os": os_name, "version": __version__},
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            verify=False,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Could not reach update API: %s", exc)
        return False

    data = resp.json()

    if not data.get("update_available"):
        logger.info("wright-telemetry is up to date (v%s)", __version__)
        return True

    latest_version = data.get("latest_version", "unknown")
    download_url = data.get("download_url")
    checksum_url = data.get("checksum_url")

    if not download_url or not checksum_url:
        logger.warning("Update response missing download_url or checksum_url")
        return True

    logger.info(
        "Update available: v%s -> v%s. Downloading...",
        __version__,
        latest_version,
    )

    asset_name = download_url.rstrip("/").split("/")[-1]
    checksum_name = checksum_url.rstrip("/").split("/")[-1]

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        download_path = tmppath / asset_name
        checksum_path = tmppath / checksum_name
        _download(download_url, download_path)
        _download(checksum_url, checksum_path)
        _verify_checksum(download_path, checksum_path)
        new_binary = _extract_binary(download_path, tmppath)
        if new_binary is None:
            logger.warning("Could not extract binary from downloaded asset")
            return True
        _replace_and_restart(new_binary)

    # _replace_and_restart never returns (it exec's or exits), but if somehow
    # we get here, treat it as success to avoid backoff loops.
    return True


# ── Helpers (unchanged) ──────────────────────────────────────────────────


def _is_newer(latest: str, current: str) -> bool:
    """Return True if latest version tuple is greater than current."""
    try:
        return tuple(int(x) for x in latest.split(".")) > tuple(
            int(x) for x in current.split(".")
        )
    except ValueError:
        return False


def _verify_checksum(download_path: Path, checksum_path: Path) -> None:
    """Raise ValueError if the SHA256 of download_path doesn't match checksum_path."""
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
        for candidate in ("wright-telemetry", "wright-telemetry.exe"):
            binary = workdir / candidate
            if binary.exists():
                return binary
        return None

    # Linux: bare binary (e.g. wright-telemetry-linux)
    return asset_path


def _replace_and_restart(new_binary: Path) -> None:
    current = Path(sys.executable)
    if sys.platform == "win32":
        _replace_and_restart_windows(new_binary, current)
    else:
        _replace_and_restart_unix(new_binary, current)


def _replace_and_restart_unix(new_binary: Path, current: Path) -> None:
    new_binary.chmod(0o755)
    staged = current.with_suffix(".new")
    shutil.copy2(new_binary, staged)
    staged.chmod(0o755)
    os.rename(staged, current)
    logger.info("Update applied. Restarting...")
    # Tell the restarted process which config to use so it skips the
    # interactive config-location prompt entirely.
    try:
        from wright_telemetry.config import CONFIG_FILE
        os.environ["WRIGHT_CONFIG"] = str(CONFIG_FILE)
    except Exception:
        pass
    os.execv(str(current), sys.argv)


def _replace_and_restart_windows(new_binary: Path, current: Path) -> None:
    staged = current.with_name(current.stem + "-update" + current.suffix)
    shutil.copy2(new_binary, staged)

    # Propagate the active config path so the restarted process skips the
    # interactive config-location prompt.  os.environ is inherited by the
    # PowerShell child which in turn passes it to Start-Process.
    try:
        from wright_telemetry.config import CONFIG_FILE
        os.environ["WRIGHT_CONFIG"] = str(CONFIG_FILE)
    except Exception:
        pass

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
