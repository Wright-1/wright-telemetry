#!/usr/bin/env bash
# =============================================================================
# build_linux.sh — Build WrightData Linux packages
#
# Produces:
#   dist/WrightData-<version>-linux-x86_64.tar.gz   ← portable, runs anywhere
#   dist/wrightdata_<version>_amd64.deb              ← Debian/Ubuntu package
#
# Requirements:
#   • Python 3.x + pip
#   • PyInstaller     (pip install pyinstaller)
#   • PyQt6           (pip install PyQt6)            ← GUI
#   • pyfiglet        (pip install pyfiglet)
#   • dpkg-deb        (apt install dpkg  — for .deb; skipped if absent)
#
# Usage:
#   ./build_linux.sh               # full build: PyInstaller + tar.gz + .deb
#   ./build_linux.sh --no-deb      # skip .deb packaging
#   ./build_linux.sh --deb-only    # skip PyInstaller, repackage existing dist/
# =============================================================================
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
APP_NAME="WrightData"
SPEC_FILE="wright-telemetry.spec"
DIST_DIR="dist"
BUILD_DIR="build"

BUILD_APP=true
BUILD_DEB=true

for arg in "$@"; do
  case "$arg" in
    --no-deb)   BUILD_DEB=false ;;
    --deb-only) BUILD_APP=false ;;
  esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────
info() { printf '  \033[34m✦\033[0m  %s\n' "$*"; }
ok()   { printf '  \033[32m✔\033[0m  %s\n' "$*"; }
warn() { printf '  \033[33m⚠\033[0m  %s\n' "$*" >&2; }
die()  { printf '  \033[31m✘\033[0m  %s\n' "$*" >&2; exit 1; }
hr()   { printf '\033[90m'; printf '%0.s─' {1..70}; printf '\033[0m\n'; }

hr
printf '  \033[1mWRIGHT TELEMETRY\033[0m — Linux Build Script\n'
hr

# ── Guard: Linux only ─────────────────────────────────────────────────────────
[[ "$(uname)" == "Linux" ]] || die "This script must be run on Linux."

# ── Locate Python ─────────────────────────────────────────────────────────────
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || die "python3 not found. Install it and try again."
info "Using Python: $PYTHON ($(${PYTHON} --version))"

PIP="$PYTHON -m pip"

# ── Derive version ────────────────────────────────────────────────────────────
VERSION=$(PYTHONPATH="$(dirname "$0")" "$PYTHON" -c \
  "from wright_telemetry import __version__; print(__version__)")
info "Version: ${VERSION}"

# ── Check / install dependencies ─────────────────────────────────────────────
info "Checking Python dependencies…"

check_import() {
  local pkg="$1" import_name="${2:-$1}"
  if "$PYTHON" -c "import ${import_name}" &>/dev/null; then
    ok "${pkg} ✓"
  else
    warn "${pkg} missing — installing…"
    $PIP install --quiet "${pkg}"
    ok "${pkg} installed"
  fi
}

check_import "pyinstaller"  "PyInstaller"
check_import "PyQt6"        "PyQt6"
check_import "pyfiglet"     "pyfiglet"

# ── Generate a PNG icon for the desktop entry (if missing) ────────────────────
if [[ ! -f "assets/wright-telemetry.png" ]]; then
  info "Generating app icon PNG…"
  "$PYTHON" assets/make_app_icon_win.py   # reuses the same painter; outputs .png
  ok "Icon ready: assets/wright-telemetry.png"
else
  ok "Icon already exists: assets/wright-telemetry.png"
fi

# ── PyInstaller build ─────────────────────────────────────────────────────────
if $BUILD_APP; then
  info "Running PyInstaller (this takes a minute)…"
  rm -rf "${BUILD_DIR}" "${DIST_DIR}"
  "$PYTHON" -m PyInstaller "${SPEC_FILE}" \
    --noconfirm \
    --log-level WARN
  ok "PyInstaller finished"

  [[ -d "${DIST_DIR}/wright-telemetry-gui" ]] || \
    die "Expected ${DIST_DIR}/wright-telemetry-gui not found."
  ok "App bundle ready: ${DIST_DIR}/wright-telemetry-gui/"
fi

# ── tar.gz — portable bundle ──────────────────────────────────────────────────
TARBALL="${DIST_DIR}/${APP_NAME}-${VERSION}-linux-x86_64.tar.gz"
info "Creating portable tar.gz…"
rm -f "${TARBALL}"
tar -czf "${TARBALL}" \
  -C "${DIST_DIR}" \
  "wright-telemetry-gui" \
  "wright-telemetry"
ok "Portable archive: ${TARBALL}"
TAR_SIZE=$(du -sh "${TARBALL}" | cut -f1)
ok "Archive size: ${TAR_SIZE}"

# ── .deb package ─────────────────────────────────────────────────────────────
if $BUILD_DEB; then
  if ! command -v dpkg-deb >/dev/null 2>&1; then
    warn "dpkg-deb not found — skipping .deb build."
    warn "Install with: sudo apt install dpkg"
  else
    DEB_ROOT="${DIST_DIR}/deb-staging"
    DEB_INSTALL="${DEB_ROOT}/opt/wrightdata"
    DEB_BIN="${DEB_ROOT}/usr/local/bin"
    DEB_APPS="${DEB_ROOT}/usr/share/applications"
    DEB_ICONS="${DEB_ROOT}/usr/share/pixmaps"
    DEB_CTRL="${DEB_ROOT}/DEBIAN"

    rm -rf "${DEB_ROOT}"
    mkdir -p "${DEB_INSTALL}" "${DEB_BIN}" "${DEB_APPS}" "${DEB_ICONS}" "${DEB_CTRL}"

    # Copy app files
    cp -r "${DIST_DIR}/wright-telemetry-gui/." "${DEB_INSTALL}/"
    cp "${DIST_DIR}/wright-telemetry" "${DEB_BIN}/wright-telemetry"
    chmod +x "${DEB_BIN}/wright-telemetry"
    cp "assets/wright-telemetry.png" "${DEB_ICONS}/wrightdata.png"

    # Launcher wrapper
    cat > "${DEB_BIN}/wrightdata" <<'WRAPPER'
#!/usr/bin/env bash
exec /opt/wrightdata/wright-telemetry-gui "$@"
WRAPPER
    chmod +x "${DEB_BIN}/wrightdata"

    # .desktop entry
    cat > "${DEB_APPS}/wrightdata.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=WrightData
GenericName=Mining Telemetry
Comment=Monitor and manage Bitcoin miners
Exec=/usr/local/bin/wrightdata
Icon=wrightdata
Terminal=false
Categories=Utility;Network;
StartupWMClass=wrightdata
DESKTOP

    # DEBIAN/control
    cat > "${DEB_CTRL}/control" <<CTRL
Package: wrightdata
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: amd64
Maintainer: Wright One <support@wrightone.io>
Homepage: https://wrightone.io
Description: WrightData Mining Telemetry
 Monitor and manage Bitcoin miners from a single desktop app.
 Supports Braiins OS, Bitmain, LuxOS, and VNish firmware.
CTRL

    # Post-install: refresh desktop database
    cat > "${DEB_CTRL}/postinst" <<'POSTINST'
#!/bin/sh
update-desktop-database -q /usr/share/applications 2>/dev/null || true
exit 0
POSTINST
    chmod 755 "${DEB_CTRL}/postinst"

    DEB_OUT="${DIST_DIR}/wrightdata_${VERSION}_amd64.deb"
    dpkg-deb --build "${DEB_ROOT}" "${DEB_OUT}"
    ok ".deb package: ${DEB_OUT}"
    DEB_SIZE=$(du -sh "${DEB_OUT}" | cut -f1)
    ok ".deb size: ${DEB_SIZE}"

    rm -rf "${DEB_ROOT}"
  fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
hr
echo ""
printf '  \033[1m✅  BUILD COMPLETE\033[0m\n'
echo ""
printf '  \033[34m📦\033[0m  Distributable packages:\n'
printf '    \033[32m•\033[0m  %s\n' "${TARBALL}"
if $BUILD_DEB && command -v dpkg-deb >/dev/null 2>&1; then
  printf '    \033[32m•\033[0m  %s\n' "${DIST_DIR}/wrightdata_${VERSION}_amd64.deb"
fi
echo ""
echo "  Install instructions:"
printf '    \033[32m•\033[0m  tar.gz: extract anywhere, run wright-telemetry-gui/wright-telemetry-gui\n'
if $BUILD_DEB && command -v dpkg-deb >/dev/null 2>&1; then
  printf '    \033[32m•\033[0m  .deb:   sudo dpkg -i dist/wrightdata_*.deb\n'
fi
echo ""
hr
echo ""
