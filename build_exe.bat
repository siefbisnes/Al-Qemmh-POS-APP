@echo off
setlocal
REM Builds AlQemma.exe and collects everything needed into one folder:
REM Program\
REM
REM Run this from the project folder, after "pip install -r requirements.txt".

set SCRIPT_DIR=%~dp0
set OUTPUT_DIR=%SCRIPT_DIR%Program
set APP_EXE=%OUTPUT_DIR%\AlQemma.exe

if not exist "%SCRIPT_DIR%vendor\MicrosoftEdgeWebView2RuntimeInstallerX64.exe" (
    echo.
    echo ERROR: vendor\MicrosoftEdgeWebView2RuntimeInstallerX64.exe is missing.
    echo Download the "Evergreen Standalone Installer, x64" from:
    echo   https://developer.microsoft.com/microsoft-edge/webview2/
    echo and save it at that path before building - see BUILD_EXE.md.
    echo.
    goto :error
)

if exist "%OUTPUT_DIR%" rmdir /s /q "%OUTPUT_DIR%"
mkdir "%OUTPUT_DIR%"

echo === Building AlQemma.exe ===
pyinstaller --noconfirm alqemma.spec
if errorlevel 1 goto :error

echo.
echo === Preparing offline package in Program ===
copy /Y "%SCRIPT_DIR%dist\AlQemma.exe" "%APP_EXE%" >nul

REM playwright_browsers is bundled straight into AlQemma.exe by
REM alqemma.spec's datas entry, and Chromium's exact path inside it is
REM now resolved directly in app/services/receipts.py (via
REM executable_path=), not through an env var - so nothing needs to be
REM copied or exported here for it anymore.

echo @echo off > "%OUTPUT_DIR%\AlQemma.bat"
echo set "BASE_DIR=%%~dp0" >> "%OUTPUT_DIR%\AlQemma.bat"
echo set "PYTHONUTF8=1" >> "%OUTPUT_DIR%\AlQemma.bat"
echo if exist "%%BASE_DIR%%AlQemma.exe" ( >> "%OUTPUT_DIR%\AlQemma.bat"
echo   "%%BASE_DIR%%AlQemma.exe" >> "%OUTPUT_DIR%\AlQemma.bat"
echo ) else ( >> "%OUTPUT_DIR%\AlQemma.bat"
echo   echo AlQemma.exe not found. >> "%OUTPUT_DIR%\AlQemma.bat"
echo   pause >> "%OUTPUT_DIR%\AlQemma.bat"
echo ) >> "%OUTPUT_DIR%\AlQemma.bat"

echo.
echo =====================================================
echo Build complete.
echo The final package is in: Program\
echo Run AlQemma.bat from that folder.
echo =====================================================
goto :end

:error
echo.
echo Build failed - scroll up to see the actual error from PyInstaller.

:end
pause