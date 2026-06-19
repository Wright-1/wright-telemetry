@echo off
setlocal enabledelayedexpansion
:: =============================================================================
:: build_windows.bat — Build WrightData Windows app + Inno Setup installer
::
:: Requirements (all must be on PATH or found automatically):
::   • Python 3.x with pip
::   • PyInstaller     (pip install pyinstaller)
::   • PyQt6           (pip install PyQt6)
::   • Pillow          (pip install Pillow)      ← for .ico generation
::   • Inno Setup 6+   https://jrsoftware.org/isdl.php
::
:: Usage (from repo root, in a regular cmd or PowerShell window):
::   build_windows.bat              -- full build: PyInstaller + Inno Setup
::   build_windows.bat --no-installer   -- PyInstaller only
::   build_windows.bat --installer-only -- skip PyInstaller, repackage existing dist\
::
:: The finished installer lands in:
::   dist\WrightData-Installer-<version>.exe
:: =============================================================================

:: ── Config ────────────────────────────────────────────────────────────────────
set APP_NAME=WrightData
set SPEC_FILE=wright-telemetry.spec
set DIST_DIR=dist
set BUILD_DIR=build

:: Default flags
set BUILD_APP=1
set BUILD_INSTALLER=1

:: ── Parse args ────────────────────────────────────────────────────────────────
for %%A in (%*) do (
    if /I "%%A"=="--no-installer"    set BUILD_INSTALLER=0
    if /I "%%A"=="--installer-only"  set BUILD_APP=0
)

:: ── Helpers ───────────────────────────────────────────────────────────────────
call :hr
echo   WRIGHT TELEMETRY -- Windows Build Script
call :hr

:: ── Locate Python ─────────────────────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo   [ERR] python not found on PATH. Install Python 3 and try again.
    exit /b 1
)
set PYTHON=python

:: ── Derive version ────────────────────────────────────────────────────────────
for /f "delims=" %%V in ('%PYTHON% -c "from wright_telemetry import __version__; print(__version__)"') do (
    set VERSION=%%V
)
echo   Version: %VERSION%

:: ── Check / install dependencies ─────────────────────────────────────────────
echo   Checking Python dependencies...
%PYTHON% -c "import PyInstaller" >nul 2>&1 || (
    echo   Installing pyinstaller...
    %PYTHON% -m pip install --quiet pyinstaller
)
%PYTHON% -c "import PyQt6" >nul 2>&1 || (
    echo   Installing PyQt6...
    %PYTHON% -m pip install --quiet PyQt6
)
%PYTHON% -c "import PIL" >nul 2>&1 || (
    echo   Installing Pillow...
    %PYTHON% -m pip install --quiet Pillow
)
%PYTHON% -c "import pyfiglet" >nul 2>&1 || (
    echo   Installing pyfiglet...
    %PYTHON% -m pip install --quiet pyfiglet
)

:: ── Generate Windows icon if missing ─────────────────────────────────────────
if not exist "assets\wright-telemetry.ico" (
    echo   Generating Windows icon...
    %PYTHON% assets\make_app_icon_win.py
    if errorlevel 1 ( echo   [ERR] Icon generation failed. & exit /b 1 )
    echo   Windows icon ready.
) else (
    echo   Windows icon already exists.
)

:: ── PyInstaller build ─────────────────────────────────────────────────────────
if "%BUILD_APP%"=="1" (
    echo   Running PyInstaller...
    if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
    if exist "%DIST_DIR%"  rmdir /s /q "%DIST_DIR%"

    %PYTHON% -m PyInstaller %SPEC_FILE% --noconfirm --log-level WARN
    if errorlevel 1 ( echo   [ERR] PyInstaller failed. & exit /b 1 )
    echo   PyInstaller finished.

    if not exist "%DIST_DIR%\wright-telemetry-gui" (
        echo   [ERR] Expected dist\wright-telemetry-gui not found.
        exit /b 1
    )
    echo   App bundle ready: %DIST_DIR%\wright-telemetry-gui\
)

:: ── Locate Inno Setup compiler ────────────────────────────────────────────────
if "%BUILD_INSTALLER%"=="1" (
    set ISCC=
    if exist "C:\Program Files (x86)\Inno Setup 6\iscc.exe" (
        set "ISCC=C:\Program Files (x86)\Inno Setup 6\iscc.exe"
    )
    if exist "C:\Program Files\Inno Setup 6\iscc.exe" (
        set "ISCC=C:\Program Files\Inno Setup 6\iscc.exe"
    )
    :: Also check PATH
    if not defined ISCC (
        where iscc >nul 2>&1 && set ISCC=iscc
    )

    if not defined ISCC (
        echo.
        echo   [WARN] Inno Setup not found. Skipping installer build.
        echo          Download from: https://jrsoftware.org/isdl.php
        echo          Then re-run:   build_windows.bat --installer-only
        goto :done
    )

    echo   Building installer with Inno Setup...
    "%ISCC%" /DMyAppVersion=%VERSION% installer\inno_setup.iss
    if errorlevel 1 ( echo   [ERR] Inno Setup build failed. & exit /b 1 )
    echo   Installer ready: %DIST_DIR%\WrightData-Installer-%VERSION%.exe
)

:done
echo.
call :hr
echo.
echo   BUILD COMPLETE
echo.
if "%BUILD_INSTALLER%"=="1" if defined ISCC (
    echo   Distributable installer:
    echo     %DIST_DIR%\WrightData-Installer-%VERSION%.exe
    echo.
    echo   NOTE: This build is UNSIGNED. Windows SmartScreen may warn users
    echo         on first launch. Right-click the installer and choose "Run anyway".
)
echo.
call :hr
goto :eof

:hr
echo ======================================================================
goto :eof
