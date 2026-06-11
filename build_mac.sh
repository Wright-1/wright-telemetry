#!/usr/bin/env bash
# =============================================================================
# build_mac.sh — Build WrightData macOS app bundle + distributable DMG
#
# No Homebrew required. Uses only:
#   • pyinstaller  (pip install pyinstaller)
#   • dmgbuild     (pip install dmgbuild)       ← pretty DMG with background
#   • PyQt6        (pip install PyQt6)          ← renders the background image
#   • hdiutil      (built into macOS)           ← called internally by dmgbuild
#
# Usage:
#   ./build_mac.sh              # full build: PyInstaller → background → DMG
#   ./build_mac.sh --no-dmg    # PyInstaller only (skip DMG)
#   ./build_mac.sh --dmg-only  # skip PyInstaller, re-package existing dist/
# =============================================================================
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
APP_NAME="WrightData"
APP_BUNDLE="WrightData.app"
DMG_BASENAME="WrightData-Installer"
VERSION="0.7.3"
SPEC_FILE="wright-telemetry.spec"
DIST_DIR="dist"
BUILD_DIR="build"
OUTPUT_DMG="${DIST_DIR}/${DMG_BASENAME}-${VERSION}.dmg"

# ── macOS deployment target ───────────────────────────────────────────────────
# Build with Python 3.9 (compiled for macOS 12) so the app runs on Monterey+.
# venv-build is a separate venv created from /opt/homebrew/opt/python@3.9.
export MACOSX_DEPLOYMENT_TARGET="12.0"
BUILD_VENV="$(dirname "$0")/venv-build"

BUILD_APP=true
BUILD_DMG=true

for arg in "$@"; do
  case "$arg" in
    --no-dmg)    BUILD_DMG=false ;;
    --dmg-only)  BUILD_APP=false ;;
  esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────
info() { printf '  \033[34m✦\033[0m  %s\n' "$*"; }
ok()   { printf '  \033[32m✔\033[0m  %s\n' "$*"; }
warn() { printf '  \033[33m⚠\033[0m  %s\n' "$*" >&2; }
die()  { printf '  \033[31m✘\033[0m  %s\n' "$*" >&2; exit 1; }
hr()   { printf '\033[90m'; printf '%0.s─' {1..70}; printf '\033[0m\n'; }

hr
printf '  \033[1mWRIGHT TELEMETRY\033[0m — macOS Build Script  \033[90m(v%s)\033[0m\n' "${VERSION}"
hr

# ── Guard: macOS only ─────────────────────────────────────────────────────────
[[ "$(uname)" == "Darwin" ]] || die "This script must be run on macOS."

# ── Select Python from the build venv (macOS 12-targeting Python 3.9) ─────────
if [[ -x "${BUILD_VENV}/bin/python3" ]]; then
  PYTHON="${BUILD_VENV}/bin/python3"
  info "Using build venv Python: ${PYTHON} (MACOSX_DEPLOYMENT_TARGET=${MACOSX_DEPLOYMENT_TARGET})"
else
  warn "venv-build not found at ${BUILD_VENV}"
  warn "Run: /opt/homebrew/opt/python@3.9/bin/python3.9 -m venv venv-build"
  warn "Then: MACOSX_DEPLOYMENT_TARGET=12.0 venv-build/bin/pip install -r requirements.txt pyinstaller dmgbuild"
  die  "Cannot build without the macOS-12-targeting venv. See warnings above."
fi

PIP="${PYTHON} -m pip"

# ── Verify required packages are present in the build venv ───────────────────
info "Checking Python dependencies in venv-build…"

check_import() {
  local pkg="$1"
  local import_name="${2:-$1}"
  if "${PYTHON}" -c "import ${import_name}" &>/dev/null; then
    ok "${pkg} ✓"
  else
    warn "${pkg} missing — installing into venv-build…"
    MACOSX_DEPLOYMENT_TARGET=12.0 ${PIP} install --quiet "${pkg}"
    ok "${pkg} installed"
  fi
}

check_import "pyinstaller"  "PyInstaller"
check_import "PyQt6"        "PyQt6"
check_import "dmgbuild"     "dmgbuild"

# ── 1. Generate app icon (.icns) if missing ─────────────────────────────────
# Must run before PyInstaller so the icon is embedded in WrightData.app.
if [[ ! -f "assets/wright-telemetry.icns" ]]; then
  info "Generating app icon…"
  "${PYTHON}" assets/make_app_icon.py
  ok "App icon ready: assets/wright-telemetry.icns"
else
  ok "App icon already exists: assets/wright-telemetry.icns"
fi

# ── 2. PyInstaller build ──────────────────────────────────────────────────────
if $BUILD_APP; then
  info "Running PyInstaller (this takes a minute)…"
  rm -rf "${BUILD_DIR}" "${DIST_DIR}"
  "${PYTHON}" -m PyInstaller "${SPEC_FILE}" \
    --noconfirm \
    --log-level WARN
  ok "PyInstaller finished"

  APP_PATH="${DIST_DIR}/${APP_BUNDLE}"
  [[ -d "${APP_PATH}" ]] || die "Expected bundle not found: ${APP_PATH}"
  ok "App bundle ready: ${APP_PATH}"
fi

# ── 3. Generate DMG background image ─────────────────────────────────────────
if $BUILD_DMG; then
  if [[ ! -f "assets/dmg-background.tiff" ]]; then
    info "Generating DMG background image…"
    "${PYTHON}" assets/make_dmg_background.py
    ok "Background image generated: assets/dmg-background.tiff"
  else
    ok "Background image already exists: assets/dmg-background.tiff"
  fi
fi

# ── 4. Build the pretty DMG with dmgbuild ────────────────────────────────────
if $BUILD_DMG; then
  info "Building DMG with dmgbuild…"
  rm -f "${OUTPUT_DMG}"
  mkdir -p "${DIST_DIR}"

  "${PYTHON}" -m dmgbuild \
    -s "installer/dmg_settings.py" \
    -D "version=${VERSION}" \
    "${APP_NAME} ${VERSION}" \
    "${OUTPUT_DMG}"

  ok "DMG created: ${OUTPUT_DMG}"

  # Print human-readable size
  SIZE=$(du -sh "${OUTPUT_DMG}" | cut -f1)
  ok "DMG size: ${SIZE}"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
hr
echo ""
printf '  \033[1m✅  BUILD COMPLETE\033[0m\n'
echo ""
if $BUILD_DMG; then
  printf '  \033[34m📦\033[0m  Distributable DMG:\n'
  printf '      \033[1m%s\033[0m\n' "${OUTPUT_DMG}"
  echo ""
  echo "  The DMG contains:"
  printf '    \033[32m•\033[0m  %-32s  ← drag to Applications\n' "${APP_BUNDLE}"
  printf '    \033[32m•\033[0m  %-32s  ← shortcut for easy install\n' "Applications/"
  printf '    \033[32m•\033[0m  %s\n' "Gatekeeper bypass instructions baked into background"
fi
echo ""
hr
echo ""
printf '  \033[33m⚠️\033[0m   This build is UNSIGNED. Users will see a macOS security warning\n'
printf '      on first launch. Bypass instructions are shown directly in the DMG\n'
printf '      window — no extra files needed.\n'
echo ""
