#!/usr/bin/env bash
# Build AlQemma.exe + AlQemma_Setup.exe on Linux using Wine (Windows Python
# + PyInstaller + Inno Setup). Run from anywhere:
#   ./build_exe.sh
#
# On a real Windows PC use build_exe.bat instead.

set -u
set -o pipefail

export WINEPREFIX="${WINEPREFIX:-$HOME/.wine}"
export WINEDEBUG="${WINEDEBUG:--all}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

OUTPUT_DIR="$SCRIPT_DIR/Program"
APP_EXE="$OUTPUT_DIR/AlQemma.exe"
VENDOR_WEBVIEW2="$SCRIPT_DIR/vendor/MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
VENDOR_TAILSCALE="$SCRIPT_DIR/vendor/tailscale-setup-latest-amd64.exe"
VENDOR_TAILSCALE_FALLBACK="$SCRIPT_DIR/vendor/tailscale-setup-1.102.3.exe"
VC_REDIST="$SCRIPT_DIR/VC_redist.x86.exe"
INNO_INSTALLER="$SCRIPT_DIR/innosetup-6.7.3.exe"
PYTHON_INSTALLER="$SCRIPT_DIR/python-3.11.9-amd64.exe"

fail() {
    echo ""
    echo "ERROR: $*"
    echo "Build failed."
    exit 1
}

require_file() {
    local path="$1"
    local hint="$2"
    if [ ! -f "$path" ]; then
        echo ""
        echo "ERROR: $(basename "$path") is missing."
        echo "$hint"
        echo ""
        exit 1
    fi
}

find_iscc() {
    local candidate
    for candidate in \
        "$WINEPREFIX/drive_c/Program Files (x86)/Inno Setup 6/ISCC.exe" \
        "$WINEPREFIX/drive_c/Program Files/Inno Setup 6/ISCC.exe" \
        "$WINEPREFIX/drive_c/users/$USER/AppData/Local/Programs/Inno Setup 6/ISCC.exe"
    do
        if [ -f "$candidate" ]; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    return 1
}

wine_python() {
    wine python "$@"
}

echo "=== AlQemma Windows exe build (Wine) ==="
echo "Project: $SCRIPT_DIR"
echo "WINEPREFIX: $WINEPREFIX"
echo ""

command -v wine >/dev/null 2>&1 || fail "wine is not installed. Install Wine, then rerun."

require_file "$VENDOR_WEBVIEW2" \
    "Download the Evergreen Standalone Installer (x64) from
  https://developer.microsoft.com/microsoft-edge/webview2/
and save it as:
  $VENDOR_WEBVIEW2"

if [ ! -f "$VENDOR_TAILSCALE" ] && [ -f "$VENDOR_TAILSCALE_FALLBACK" ]; then
    cp "$VENDOR_TAILSCALE_FALLBACK" "$VENDOR_TAILSCALE"
fi

require_file "$VENDOR_TAILSCALE" \
    "Download the current Windows installer from
  https://tailscale.com/download/windows
rename it to tailscale-setup-latest-amd64.exe and save it as:
  $VENDOR_TAILSCALE"

require_file "$VC_REDIST" \
    "Download the Microsoft Visual C++ Redistributable and save it as:
  $VC_REDIST"

require_file "$SCRIPT_DIR/alqemma.spec" "alqemma.spec is required."
require_file "$SCRIPT_DIR/AlQemma.iss" "AlQemma.iss is required."
require_file "$SCRIPT_DIR/license.txt" "license.txt is required by Inno Setup."
require_file "$SCRIPT_DIR/app/static/app_icon.ico" "app/static/app_icon.ico is required by alqemma.spec."

if ! wine_python --version >/dev/null 2>&1; then
    [ -f "$PYTHON_INSTALLER" ] || fail "Windows Python is not available in Wine, and $PYTHON_INSTALLER is missing."
    echo "=== Installing Python 3.11 into Wine ==="
    wine "$PYTHON_INSTALLER" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1 SimpleInstall=1 \
        || fail "Python installer failed under Wine."
    wine_python --version >/dev/null 2>&1 || fail "Python is still not available as 'wine python' after install."
fi

echo "Wine Python: $(wine_python --version 2>/dev/null | tr -d '\r')"

echo ""
echo "=== Installing Windows Python packages ==="
wine_python -m pip install --upgrade pip >/dev/null \
    || fail "pip upgrade failed under Wine."
wine_python -m pip install -r "$SCRIPT_DIR/requirements.txt" pyinstaller pythonnet \
    || fail "pip install of application dependencies failed."
# pywin32 is used for desktop/startup shortcuts. Installing it under Wine
# can fail even though the hiddenimports still compile; try, then continue.
if ! wine_python -m pip install pywin32; then
    echo "WARNING: pywin32 did not install under Wine. Shortcuts may be missing in the frozen exe; the app itself can still build."
fi
wine_python -m PyInstaller --version >/dev/null 2>&1 || fail "PyInstaller is not importable via 'wine python -m PyInstaller'."

ISCC_EXE="$(find_iscc || true)"
if [ -z "$ISCC_EXE" ]; then
    [ -f "$INNO_INSTALLER" ] || fail "Inno Setup is not installed in Wine, and $INNO_INSTALLER is missing."
    echo ""
    echo "=== Installing Inno Setup 6 into Wine ==="
    wine "$INNO_INSTALLER" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- \
        || fail "Inno Setup installer failed under Wine."
    ISCC_EXE="$(find_iscc || true)"
    [ -n "$ISCC_EXE" ] || fail "ISCC.exe was not found after installing Inno Setup."
fi
echo "Inno Setup: $ISCC_EXE"

if [ -d "$OUTPUT_DIR" ]; then
    rm -rf "$OUTPUT_DIR"
fi
mkdir -p "$OUTPUT_DIR"

echo ""
echo "=== Building AlQemma.exe ==="
wine_python -m PyInstaller --noconfirm --distpath "$SCRIPT_DIR/dist" --workpath "$SCRIPT_DIR/build" alqemma.spec
if [ $? -ne 0 ]; then
    fail "PyInstaller failed. Scroll up for the actual error."
fi
[ -f "$SCRIPT_DIR/dist/AlQemma.exe" ] || fail "PyInstaller finished without creating dist/AlQemma.exe."

echo ""
echo "=== Preparing offline package in Program ==="
cp "$SCRIPT_DIR/dist/AlQemma.exe" "$APP_EXE"

BAT_FILE="$OUTPUT_DIR/AlQemma.bat"
printf "@echo off\r\n" > "$BAT_FILE"
printf "set \"BASE_DIR=%%~dp0\"\r\n" >> "$BAT_FILE"
printf "set \"PYTHONUTF8=1\"\r\n" >> "$BAT_FILE"
printf "if exist \"%%BASE_DIR%%AlQemma.exe\" (\r\n" >> "$BAT_FILE"
printf "  \"%%BASE_DIR%%AlQemma.exe\"\r\n" >> "$BAT_FILE"
printf ") else (\r\n" >> "$BAT_FILE"
printf "  echo AlQemma.exe not found.\r\n" >> "$BAT_FILE"
printf "  pause\r\n" >> "$BAT_FILE"
printf ")\r\n" >> "$BAT_FILE"

echo ""
echo "=== Creating installer ==="
mkdir -p "$SCRIPT_DIR/installer"
rm -f "$SCRIPT_DIR/installer/AlQemma_Setup.exe"

ISCC_WIN="$(winepath -w "$ISCC_EXE")"
ISS_WIN="$(winepath -w "$SCRIPT_DIR/AlQemma.iss")"
wine "$ISCC_WIN" "$ISS_WIN"
if [ $? -ne 0 ]; then
    fail "Inno Setup compilation failed."
fi
[ -f "$SCRIPT_DIR/installer/AlQemma_Setup.exe" ] || fail "Inno Setup finished without creating installer/AlQemma_Setup.exe."

echo ""
echo "====================================================="
echo "Build complete."
echo "The final package is in: Program/"
echo "Run AlQemma.bat from that folder on Windows."
echo "The installer is in: installer/AlQemma_Setup.exe"
echo "====================================================="
