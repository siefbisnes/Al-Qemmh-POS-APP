import os
import sys
import zipfile
import tempfile


def get_base_dir():
    if getattr(sys, "frozen", False):
        return sys._MEIPASS

    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()

ZIP_PATH = os.path.join(
    BASE_DIR,
    "playwright_browsers.zip"
)


# Use a stable temporary directory for this application.
BROWSER_DIR = os.path.join(
    tempfile.gettempdir(),
    "AlQemma",
    "playwright_browsers"
)


def prepare_playwright_browsers():
    chromium_exe = os.path.join(
        BROWSER_DIR,
        "chromium-1194",
        "chrome-win",
        "chrome.exe"
    )

    if os.path.exists(chromium_exe):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = BROWSER_DIR
        return

    os.makedirs(BROWSER_DIR, exist_ok=True)

    with zipfile.ZipFile(ZIP_PATH, "r") as archive:
        archive.extractall(BROWSER_DIR)

    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = BROWSER_DIR


prepare_playwright_browsers()