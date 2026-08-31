# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules


# ============================================================
# AlQemma - PyInstaller specification
# ============================================================

a = Analysis(
    ['run.py'],

    pathex=[],

    binaries=[],

    datas=[
        # Flask templates
        ('app/templates', 'app/templates'),

        # Static files (includes app/static/fonts/ - the bundled Noto
        # Naskh Arabic font used by the reportlab-based PDF generator
        # in app/services/pdf_utils.py)
        ('app/static', 'app/static'),

        # Database/schema
        ('schema.sql', '.'),

        # Offline WebView2 Runtime installer - shop PCs run with no/
        # restricted internet, so run.py's ensure_webview2_runtime()
        # installs from this local file instead of downloading at
        # launch. Must be downloaded once and placed here before
        # building - see BUILD_EXE.md. build_exe.bat checks for it.
        ('vendor/MicrosoftEdgeWebView2RuntimeInstallerX64.exe', 'vendor'),

        # Offline Tailscale Windows installer - same reasoning as
        # WebView2 above: a brand-new/freshly-imaged Windows machine may
        # have no internet yet and/or an unready winget, both of which
        # the old winget-based install in perform_tailscale_repair()
        # depends on. run.py's TAILSCALE_OFFLINE_INSTALLER_PATH tries
        # this local file first. Download the current
        # "tailscale-setup-<version>-amd64.exe" from
        # https://tailscale.com/download/windows, rename it to
        # tailscale-setup-latest-amd64.exe, and place it here before
        # building - see BUILD_EXE.md. build_exe.bat checks for it.
        ('vendor/tailscale-setup-latest-amd64.exe', 'vendor'),
    ],

    hiddenimports=[
        # Pillow
        'PIL._tkinter_finder',

        # PyWebView
        'webview.platforms.edgechromium',
        'webview.platforms.winforms',

        # Python.NET / CLR
        'clr_loader',
        'pythonnet',

        # Waitress
        'waitress.server',

        # Plyer
        *collect_submodules('plyer'),

        # pywin32 - used by ensure_application_shortcuts() in run.py to
        # create/update the Desktop and Startup .lnk shortcuts via the
        # WScript.Shell COM interface.
        'win32com',
        'win32com.client',
        'win32timezone',
        'pythoncom',
        'pywintypes',

        # PDF generation (app/services/pdf_utils.py, app/services/
        # receipts.py) - replaces the old Playwright/Chromium approach
        # entirely, no bundled browser needed anymore.
        'reportlab.pdfbase._fontdata',
        *collect_submodules('reportlab.pdfbase'),
        'arabic_reshaper',
        'bidi',
        'bidi.algorithm',
    ],

    hookspath=[],

    # Playwright's runtime hook (playwright_runtime.py, which extracted
    # the bundled Chromium ZIP and set PLAYWRIGHT_BROWSERS_PATH) has
    # been removed along with Playwright itself - see
    # app/services/receipts.py's module docstring. This was almost
    # certainly the actual source of "can't import it" during the exe
    # build: that hook runs BEFORE run.py even starts and would fail
    # the whole build/launch if playwright wasn't importable in the
    # PyInstaller environment. No runtime hooks are needed anymore.
    runtime_hooks=[],

    excludes=[],

    noarchive=False,
)


# ============================================================
# PYZ
# ============================================================

pyz = PYZ(
    a.pure
)


# ============================================================
# EXE
# ============================================================

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,

    [],

    name='AlQemma',

    debug=False,

    bootloader_ignore_signals=False,

    strip=False,

    upx=False,

    console=False,

    disable_windowed_traceback=False,

    argv_emulation=False,

    target_arch=None,

    codesign_identity=None,

    entitlements_file=None,

    icon='app/static/app_icon.ico',
)