"""
Al-Qemma launcher — Phase 5 + 6 (rebuild from scratch)

Phase 1 was the bare floor: splash screen, Flask/Waitress boot, redirect,
console fallback, file logging. Confirmed stable on Windows.

Phase 2 added the js_api bridge, window size/resizable persistence, and
theme persistence. Confirmed stable on Windows.

Phase 3 added real backup execution and routed the native OS close button
through the same graceful-shutdown path as the in-app Exit button.
Confirmed stable on Windows.

Phase 5 + 6 (this file) fill in lan_url and public_url:
  - get_lan_ip(): a plain UDP-socket trick (no packet actually sent, no
    subprocess) - low risk, matches "phase 5 is simple" assessment
  - Tailscale detection (get_tailscale_ip, get_tailscale_dns_name,
    start_tailscale_serve): every call goes through one hardened
    subprocess wrapper - fixed timeout, CREATE_NO_WINDOW on Windows so no
    console flash, and missing-binary/timeout/non-zero-exit are all just
    treated as "tailscale not available", never raised
  - Both run inside ServerController.detect_network(), kicked off in a
    background daemon thread from _startup_sequence AFTER the window has
    already redirected into the app - so even if Tailscale hangs or
    misbehaves, it cannot delay startup or block the splash screen. Settings
    page just shows "not available" for public_url until detection finishes,
    same honest-placeholder behavior as before.

This was the last phase in the original plan - LAN/Tailscale were always
going in last specifically because they were the most suspected. If
Windows stays stable through this, the rebuild is feature-complete.

Also added: AppAPI.export_category_products() - writes a category's
products to a .txt file ("model name: priceEGP" per line) via pywebview's
native Save dialog rather than a Flask response. This sidesteps the old
bug entirely (a text export rendering inline as a blank page instead of
downloading, because the Flask route was missing a Content-Disposition
header) - there's no HTTP response in this path at all, so that failure
mode can't happen here.
"""

import os
import sys
import time
import json
import socket
import logging
import datetime
import threading
import subprocess
import webbrowser
import urllib.error
import urllib.parse
import urllib.request

from config import Config, BUNDLE_DIR

try:
    import webview
    HAS_GUI = True
    _GUI_IMPORT_ERROR = None
except ImportError as exc:
    HAS_GUI = False
    _GUI_IMPORT_ERROR = exc


# ============================================================
# Paths & constants
# ============================================================
BASE_DIR = (
    os.path.dirname(os.path.abspath(sys.executable))
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
LOG_PATH = os.path.join(BASE_DIR, "launcher.log")
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
SETTINGS_PATH = os.path.join(INSTANCE_DIR, "launcher_settings.json")

HOST = "0.0.0.0"
PORT = 5000

WINDOW_TITLE = "Al-Qemma"
WINDOW_MIN_SIZE = (760, 560)
WINDOW_MAX_SIZE = (3840, 2160)
DEFAULT_WINDOW_WIDTH = 1080
DEFAULT_WINDOW_HEIGHT = 720

FETCH_LOGS_MAX_LINES = 400
DEFAULT_BACKUP_DIR = os.path.join(os.path.expanduser("~"), "Documents", "AlQemma Backups")

TAILSCALE_CMD_TIMEOUT = 3       # seconds, for ip/status lookups
TAILSCALE_SERVE_TIMEOUT = 6     # seconds, for the serve command itself
TAILSCALE_INSTALL_TIMEOUT = 300  # seconds - installers can be slow (download + install)

# Standalone Windows Tailscale installer, bundled into the exe via
# alqemma.spec's datas entry - same pattern as
# WEBVIEW2_OFFLINE_INSTALLER_PATH further down this file. Shop PCs run
# with no/restricted internet, so the previous winget-based install
# (_tailscale_install_command) reliably fails on a brand-new machine
# that has no internet yet, or whose winget/App Installer isn't fully
# set up (both are realistic on a freshly-imaged Windows box, even
# though winget ships with modern Windows). This local file needs no
# network and no winget at all. Download the current
# "tailscale-setup-<version>-amd64.exe" from
# https://tailscale.com/download/windows and place it here before
# building - see BUILD_EXE.md. build_exe.bat checks for it.
TAILSCALE_OFFLINE_INSTALLER_PATH = os.path.join(BUNDLE_DIR, "vendor", "tailscale-setup-latest-amd64.exe")

# Passes of "scan for a device still using our hostname and delete it"
# to keep running even AFTER the hostname has already been
# successfully claimed - a second, independent layer of protection on
# top of TAILSCALE_HOSTNAME_CLAIM_ATTEMPTS's detect->clean->claim loop
# (which only retries WHILE trying to claim). This catches a duplicate
# that reappears in the brief window right after success - e.g. a
# stale device the Tailnet API hadn't finished reporting yet, or
# another one of this same machine's old orphaned registrations
# surfacing slightly late. See the extra step at the end of
# perform_tailscale_repair().
TAILSCALE_POST_SUCCESS_SWEEP_PASSES = 3

# The Tailscale device hostname this machine should always use, regardless
# of the Windows computer name or whatever name a previous `tailscale up`
# picked - see perform_tailscale_repair() and AppAPI.troubleshoot_tailscale().
TAILSCALE_HOSTNAME = "Al-Qemma-Store"
TAILSCALE_ADMIN_MACHINES_URL = "https://login.tailscale.com/admin/machines"
TAILSCALE_LOGIN_WAIT_TIMEOUT = 180  # seconds to wait for the user to finish browser sign-in

# Tailscale API (OAuth client-credentials) - used only for the automated
# stale-duplicate-device cleanup in perform_tailscale_repair(). Credentials
# come from Config.TAILSCALE_OAUTH_* (see config.py); if they're not set,
# the repair flow falls back to the old report-only behavior. See
# _get_tailscale_oauth_token()/find_duplicate_device_via_api() below.
TAILSCALE_API_BASE = "https://api.tailscale.com/api/v2"
TAILSCALE_OAUTH_TOKEN_URL = f"{TAILSCALE_API_BASE}/oauth/token"
TAILSCALE_API_TIMEOUT = 10       # seconds, per HTTP call to the Tailscale API
TAILSCALE_API_RETRIES = 2        # extra attempts on transient failures (not on 4xx)
TAILSCALE_API_RETRY_DELAY = 2    # seconds between retries
TAILSCALE_POST_DELETE_SETTLE = 2  # seconds to wait before re-querying after a delete

# How many full detect-conflict -> clean-up -> claim-hostname -> verify
# passes perform_tailscale_repair will run before giving up on THIS
# invocation. More than 1 is needed because a single pass isn't reliable
# enough on its own: even after the API confirms a duplicate device is
# deleted, Tailscale's control-plane hostname-uniqueness index can take
# a moment longer to actually release the name, so the very next
# `tailscale set --hostname=` can still lose that race and get silently
# suffixed ("-1", "-2", ...) - and once that's happened, THIS device is
# now itself stuck on the suffixed name until someone reruns the fix.
# Retrying the whole sequence (not just the final claim) self-heals
# that without requiring the user to click "Fix Problems" again.
TAILSCALE_HOSTNAME_CLAIM_ATTEMPTS = 3

# How long a same-hostname candidate device must have been unseen before
# it's treated as safely-dead rather than possibly-still-live. Repeatedly
# re-imaging/rebuilding this app's target machine (e.g. during testing)
# leaves behind one orphaned Tailscale node per rebuild, all self-
# reporting the same hostname - see find_duplicate_device_via_api.
TAILSCALE_STALE_LASTSEEN_SECONDS = 15 * 60  # 15 minutes

# Background connectivity monitor (internet + Tailscale), independent of
# the one-shot LAN/Tailscale-serve detection above - see
# ServerController.start_connectivity_monitor().
CONNECTIVITY_CHECK_INTERVAL = 60   # seconds between checks
INTERNET_CHECK_HOST = "8.8.8.8"
INTERNET_CHECK_PORT = 53
INTERNET_CHECK_TIMEOUT = 2         # seconds

STARTUP_STEPS = [
    "Initializing...",
    "Starting local server...",
    "Setting up shortcuts...",
    "Done.",
]

SHORTCUT_NAME = "AlQemma Store.lnk"
SHORTCUT_DESCRIPTION = "AlQemma Store"

DEFAULT_SETTINGS = {
    "window_width": DEFAULT_WINDOW_WIDTH,
    "window_height": DEFAULT_WINDOW_HEIGHT,
    "resizable": True,
    "theme": "light",
    "open_browser": False,
    "auto_backup_on_close": False,
    "backup_destination": "",
}


# Splash screen, embedded directly rather than loaded from a file on disk -
# pywebview's local-file resolution (used by e.g. WebView2 on Windows) can
# 404 silently if the working directory isn't exactly what BASE_DIR
# expects. Passing html= skips that lookup entirely.
LOADING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Al-Qemma</title>
<style>
  :root {
    --bg: #1E1E1E;
    --accent: #3B82F6;
    --danger: #F87171;
    --text: #FFFFFF;
    --subtext: #9CA3AF;
    --border: #333333;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0; height: 100%; background: var(--bg);
    font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, sans-serif;
    -webkit-user-select: none; user-select: none;
  }
  body { display: flex; align-items: center; justify-content: center; }
  .card { width: 360px; padding: 32px; }
  .mark {
    width: 52px; height: 52px; border-radius: 14px; background: var(--accent);
    display: flex; align-items: center; justify-content: center; font-size: 26px; margin-bottom: 16px;
  }
  h1 { margin: 0 0 18px 0; font-size: 24px; color: var(--text); }
  .track { width: 100%; height: 6px; border-radius: 3px; background: var(--border); overflow: hidden; }
  .fill { height: 100%; width: 0%; border-radius: 3px; background: var(--accent); transition: width 0.35s ease; }
  .fill.failed { background: var(--danger); }
  .status { margin-top: 12px; font-size: 13px; color: var(--subtext); }
  .status.failed { color: var(--danger); white-space: pre-wrap; }
</style>
</head>
<body>
  <div class="card">
    <div class="mark">&#128421;</div>
    <h1>Connecting...</h1>
    <div class="track"><div class="fill" id="fill"></div></div>
    <div class="status" id="status">Initializing...</div>
  </div>
  <script>
    // Called from Python (_report_step) via window.evaluate_js as each
    // startup step completes. pct is 0-100, text is the human-readable
    // step message.
    window.setStep = function (pct, text) {
      document.getElementById("fill").style.width = pct + "%";
      document.getElementById("status").textContent = text;
    };
    // Called from Python if startup fails - freezes the bar in red
    // instead of silently hanging.
    window.setFailed = function (message) {
      document.querySelector("h1").textContent = "Startup failed";
      document.getElementById("fill").classList.add("failed");
      var status = document.getElementById("status");
      status.classList.add("failed");
      status.textContent = message;
    };
  </script>
</body>
</html>
"""


# ============================================================
# Logging (in-app log viewer's data source too, via AppAPI.fetch_logs)
# ============================================================
def configure_file_logging():
    logging.basicConfig(
        filename=LOG_PATH,
        filemode="a",
        format="%(asctime)s [%(levelname)s] %(message)s",
        level=logging.INFO,
    )
    return logging.getLogger("launcher")


# ============================================================
# Settings persistence
# ============================================================
class SettingsManager:
    """Reads/writes instance/launcher_settings.json. One process only (no
    multi-instance locking) - that matches how this app is actually run."""

    def __init__(self, path=SETTINGS_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._data = dict(DEFAULT_SETTINGS)
        self.load()

    def load(self):
        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    on_disk = json.load(f)
                merged = dict(DEFAULT_SETTINGS)
                merged.update({k: v for k, v in on_disk.items() if k in DEFAULT_SETTINGS})
                self._data = merged
            except FileNotFoundError:
                self._data = dict(DEFAULT_SETTINGS)
            except (json.JSONDecodeError, OSError):
                # Corrupt or unreadable settings file - fall back to
                # defaults rather than crashing the whole launcher over a
                # broken preferences file.
                self._data = dict(DEFAULT_SETTINGS)

    def save(self):
        with self._lock:
            os.makedirs(INSTANCE_DIR, exist_ok=True)
            tmp_path = self.path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.path)  # atomic on both Windows and Linux

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def get_all(self):
        with self._lock:
            return dict(self._data)

    def set(self, **kwargs):
        with self._lock:
            self._data.update(kwargs)
        self.save()


def _clamp_window_size(width, height):
    try:
        width = int(width)
    except (TypeError, ValueError):
        width = DEFAULT_WINDOW_WIDTH
    try:
        height = int(height)
    except (TypeError, ValueError):
        height = DEFAULT_WINDOW_HEIGHT
    width = max(WINDOW_MIN_SIZE[0], min(width, WINDOW_MAX_SIZE[0]))
    height = max(WINDOW_MIN_SIZE[1], min(height, WINDOW_MAX_SIZE[1]))
    return width, height


# ============================================================
# Backup
# ============================================================
class BackupManager:
    """Copies instance/ (settings.json + the app's database, whatever
    else lives there) to a timestamped folder under a chosen destination.
    Deliberately synchronous - shop's database is small, and running this
    async would mean the window could close mid-copy."""

    def __init__(self, source_dir=INSTANCE_DIR, logger=None):
        self.source_dir = source_dir
        self.logger = logger

    def _log(self, message, level="info"):
        if self.logger:
            getattr(self.logger, level)(message)

    def validate_destination(self, destination):
        """Returns (ok, message). Unlike a strict "must already exist"
        check, this creates the destination folder if it's missing - a
        typo'd path still fails (permission/parent-missing errors surface
        immediately), but a perfectly valid path that just hasn't been
        created yet (e.g. a fresh USB drive folder) isn't treated as an
        error."""
        if not destination or not destination.strip():
            return False, "لم يتم تحديد مسار للنسخ الاحتياطي."
        destination = destination.strip()

        if not os.path.isdir(self.source_dir):
            return False, f"مجلد البيانات غير موجود: {self.source_dir}"

        if not os.path.isdir(destination):
            try:
                os.makedirs(destination, exist_ok=True)
            except Exception as exc:
                return False, f"تعذر إنشاء المجلد: {destination}\n{exc}"

        if not os.access(destination, os.W_OK):
            return False, f"لا توجد صلاحية للكتابة في: {destination}"

        return True, "المسار صالح."

    def run_backup(self, destination):
        """Returns (ok, message). Never raises - callers (manual button,
        auto-backup-on-close) both need this to fail safely rather than
        blocking app exit. Copies into
        <destination>/AlQemma_Backup_<timestamp>/instance/ - nested under
        an "instance" subfolder so a restore is just "copy this folder
        back to instance/", not a guessing game about what's inside."""
        import shutil

        ok, message = self.validate_destination(destination)
        if not ok:
            self._log(f"Backup skipped: {message}", level="error")
            return False, message

        self._log("Saving instance files...")
        destination = destination.strip()
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        backup_root = os.path.join(destination, f"AlQemma_Backup_{timestamp}")

        try:
            self._log("Creating backup...")
            shutil.copytree(self.source_dir, os.path.join(backup_root, "instance"))
        except Exception as exc:
            msg = f"فشل النسخ الاحتياطي: {exc}"
            self._log(msg, level="error")
            return False, msg

        self._log("Almost finished...")
        time.sleep(0.2)  # brief pause so fast backups don't flash the UI illegibly

        msg = f"تم إنشاء نسخة احتياطية في: {backup_root}"
        self._log(msg)
        return True, msg

    def restore_instance(self, source_dir):
        """Returns (ok, message). The counterpart to run_backup(): a
        backup folder nests everything under <backup>/instance/, so
        pointing this at that folder (or any folder shaped like it - db
        file, product images, logs, whatever else lives in instance/)
        fully replaces self.source_dir with it. Not just the database -
        the whole folder, which is the actual fix here: the old restore
        path only ever touched the .db file."""
        import shutil

        if not source_dir or not os.path.isdir(source_dir):
            return False, f"المجلد غير موجود: {source_dir}"

        if os.path.abspath(source_dir) == os.path.abspath(self.source_dir):
            return False, "لا يمكن استبدال المجلد بنفسه."

        self._log("Restoring instance files...")
        tmp_new = self.source_dir + "_restore_tmp"
        try:
            if os.path.isdir(tmp_new):
                shutil.rmtree(tmp_new)
            shutil.copytree(source_dir, tmp_new)

            if os.path.isdir(self.source_dir):
                shutil.rmtree(self.source_dir)
            os.rename(tmp_new, self.source_dir)
        except Exception as exc:
            shutil.rmtree(tmp_new, ignore_errors=True)
            msg = f"فشل استبدال البيانات: {exc}"
            self._log(msg, level="error")
            return False, msg

        msg = "تم استبدال البيانات بنجاح. الرجاء إعادة تشغيل التطبيق."
        self._log(msg)
        return True, msg


# ============================================================
# Network detection — LAN (Phase 5) + Tailscale (Phase 6)
# ============================================================
def get_lan_ip():
    """Returns this machine's LAN-facing IP, or None. Uses the classic
    UDP "connect" trick: connecting a UDP socket doesn't send any packet
    (UDP is connectionless), it just asks the OS routing table which
    local interface would be used to reach that address - so this works
    even with no internet access, and never touches the network for real.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def _run_tailscale(args, timeout=TAILSCALE_CMD_TIMEOUT):
    """Runs `tailscale <args>` and returns (ok, output). Never raises -
    every failure mode (binary not installed, PATH issue, hung command,
    non-zero exit) collapses to ok=False with a human-readable reason, so
    callers never need their own try/except around this."""
    kwargs = {}
    if sys.platform == "win32":
        # Without this, every subprocess call here would briefly flash a
        # console window on top of the app - and depending on how strict
        # antivirus/EDR software is about processes spawning consoles,
        # can also draw unwanted attention. CREATE_NO_WINDOW avoids both.
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        result = subprocess.run(
            ["tailscale"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            **kwargs,
        )
    except FileNotFoundError:
        return False, "tailscale is not installed or not on PATH"
    except subprocess.TimeoutExpired:
        return False, f"tailscale {' '.join(args)} timed out after {timeout}s"
    except Exception as exc:
        return False, f"tailscale {' '.join(args)} failed: {exc}"

    if result.returncode != 0:
        reason = (result.stderr or result.stdout or "non-zero exit").strip()
        return False, reason
    return True, result.stdout.strip()


def get_tailscale_ip():
    """Returns this machine's Tailscale IPv4 address, or None if
    Tailscale isn't installed, isn't running, or isn't logged in."""
    ok, output = _run_tailscale(["ip", "-4"])
    if not ok:
        return None, output
    ip = output.splitlines()[0].strip() if output else ""
    return (ip or None), None


def get_tailscale_dns_name():
    """Returns this machine's MagicDNS name (e.g. shop-pc.tailXXXX.ts.net),
    or None if unavailable. Preferred over the bare IP for the public URL
    since it gives a stable https:// link."""
    ok, output = _run_tailscale(["status", "--self", "--json"])
    if not ok:
        return None, output
    try:
        data = json.loads(output)
        dns_name = (data.get("Self") or {}).get("DNSName", "").rstrip(".")
        return (dns_name or None), None
    except (json.JSONDecodeError, AttributeError) as exc:
        return None, f"could not parse tailscale status output: {exc}"


def start_tailscale_serve(port):
    """Exposes the local app over Tailscale Serve (HTTPS, tailnet-only).
    Best-effort: if this fails, the app is still reachable over the raw
    tailscale IP - it just won't have the nicer https:// name.

    NOTE: `tailscale serve` syntax has changed across versions. This uses
    the syntax current as of writing (`serve --bg --https=443 <target>`);
    if your installed tailscale version differs, check `tailscale serve
    --help` and adjust the args list below.
    """
    ok, output = _run_tailscale(
        ["serve", "--bg", "--https=443", f"http://127.0.0.1:{port}"],
        timeout=TAILSCALE_SERVE_TIMEOUT,
    )
    return ok, output


def is_tailscale_installed():
    """True only if the `tailscale` binary is actually on PATH - distinct
    from is-it-running/logged-in (get_tailscale_backend_state below).
    Reuses _run_tailscale so "not found" is detected the exact same way
    everywhere else in this file already checks it."""
    ok, output = _run_tailscale(["version"])
    if ok:
        return True
    # _run_tailscale's FileNotFoundError branch always returns this exact
    # message - any other failure (timeout, non-zero exit, etc.) means
    # the binary exists but something else went wrong, so it still
    # counts as "installed".
    return "is not installed or not on PATH" not in output


def get_tailscale_backend_state():
    """Returns (state, error) where state is Tailscale's own BackendState
    string (e.g. "Running", "NeedsLogin", "Stopped"), or (None, reason) if
    the binary is missing or the command fails. "Running" is what
    get_tailscale_ip() effectively already implies when it succeeds, but
    this checks it explicitly and by name since that's the exact signal
    the connectivity monitor and troubleshoot flow need to distinguish
    "installed but not logged in" from "installed and connected"."""
    ok, output = _run_tailscale(["status", "--json"])
    if not ok:
        return None, output
    try:
        data = json.loads(output)
        return data.get("BackendState"), None
    except (json.JSONDecodeError, AttributeError) as exc:
        return None, f"could not parse tailscale status output: {exc}"


def set_tailscale_hostname(hostname):
    """Sets this node's Tailscale device hostname via `tailscale set
    --hostname=<name>`. This is distinct from the OS/Windows hostname -
    it only changes the name Tailscale advertises for this device
    (MagicDNS name, admin console listing), which is what actually
    determines the public URL. Requires the node to already be
    installed and logged in. Returns (ok, output), same contract as
    _run_tailscale."""
    return _run_tailscale(["set", f"--hostname={hostname}"], timeout=TAILSCALE_CMD_TIMEOUT)


def get_tailscale_status_full():
    """Returns (self_info, peers, error) from `tailscale status --json`:
    self_info is the Self dict (includes HostName, ID, DNSName, ...),
    peers is the Peer dict keyed by node ID, one entry per other device
    in the tailnet this node can see. (None, None, reason) on failure -
    never raises."""
    ok, output = _run_tailscale(["status", "--json"])
    if not ok:
        return None, None, output
    try:
        data = json.loads(output)
        return data.get("Self") or {}, data.get("Peer") or {}, None
    except (json.JSONDecodeError, AttributeError) as exc:
        return None, None, f"could not parse tailscale status output: {exc}"


def find_conflicting_tailscale_device(hostname, self_id=None):
    """Looks through this node's visible peers (via `tailscale status
    --json`) for another device already using `hostname`. Returns
    (conflicting_peer_or_None, error) - the peer dict (if any) includes
    at least HostName/DNSName/Online/LastSeen, enough to show the user
    which device to check.

    Never attempts to remove anything itself - the `tailscale` CLI has no
    device-deletion command. Real deletion (see find_duplicate_device_via_api/
    remove_tailscale_device_via_api below) goes through the Tailscale admin
    API and only runs if a Tailscale OAuth client is configured; otherwise
    the correct behavior here is still to report the conflict and point at
    the admin console (TAILSCALE_ADMIN_MACHINES_URL) for the user to
    resolve by hand."""
    self_info, peers, err = get_tailscale_status_full()
    if err:
        return None, err
    my_id = self_id or (self_info or {}).get("ID")
    for peer_id, peer in (peers or {}).items():
        if peer_id == my_id:
            continue
        if (peer.get("HostName") or "").strip().lower() == hostname.lower():
            return peer, None
    return None, None


# ============================================================
# Tailscale API (OAuth client-credentials) - automated stale-duplicate
# cleanup for perform_tailscale_repair(). Everything here is additive to
# the CLI-only functions above: hostname is still set via `tailscale set`
# (set_tailscale_hostname), the API is used only to list/delete devices,
# which the CLI cannot do. If TAILSCALE_OAUTH_CLIENT_ID/SECRET aren't
# configured, every function below fails safe with a clear error and
# nothing is ever deleted.
# ============================================================
_tailscale_oauth_cache = {"token": None, "expires_at": 0.0}
_tailscale_oauth_lock = threading.Lock()


def _get_tailscale_oauth_token():
    """Returns (token, error). Exchanges the configured OAuth client
    credentials for a short-lived Tailscale API access token (client
    credentials grant) and caches it in memory only for its reported
    lifetime - never written to disk, never logged. Returns
    (None, "...not configured...") if no OAuth client is set up.
    The client secret is never included in the returned error string."""
    client_id = Config.TAILSCALE_OAUTH_CLIENT_ID
    client_secret = Config.TAILSCALE_OAUTH_CLIENT_SECRET
    if not client_id or not client_secret:
        return None, "Tailscale OAuth credentials are not configured."

    with _tailscale_oauth_lock:
        now = time.time()
        if _tailscale_oauth_cache["token"] and now < _tailscale_oauth_cache["expires_at"]:
            return _tailscale_oauth_cache["token"], None

        body = urllib.parse.urlencode({
            "client_id": client_id,
            "client_secret": client_secret,
        }).encode("utf-8")
        req = urllib.request.Request(
            TAILSCALE_OAUTH_TOKEN_URL,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TAILSCALE_API_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Deliberately no exc body/message here - an OAuth error
            # response could conceivably echo request details back.
            return None, f"Tailscale OAuth token request failed (HTTP {exc.code})."
        except urllib.error.URLError:
            return None, "Could not reach the Tailscale OAuth endpoint."
        except Exception:
            return None, "Unexpected error obtaining a Tailscale API token."

        token = payload.get("access_token")
        expires_in = payload.get("expires_in")
        if not token:
            return None, "Tailscale OAuth response did not include an access token."

        # Refresh a little early so a near-expiry token is never handed
        # to a caller mid-use.
        ttl = max(int(expires_in) - 30, 30) if isinstance(expires_in, (int, float)) else 30
        _tailscale_oauth_cache["token"] = token
        _tailscale_oauth_cache["expires_at"] = now + ttl
        return token, None


def _tailscale_api_call(method, path, token, body=None):
    """One Tailscale API call. Returns (data, error) - data is the parsed
    JSON response (or {} for an empty 2xx body). Never raises, never
    includes the bearer token in its output."""
    req = urllib.request.Request(
        f"{TAILSCALE_API_BASE}{path}",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TAILSCALE_API_TIMEOUT) as resp:
            raw = resp.read()
            return (json.loads(raw.decode("utf-8")) if raw else {}), None
    except urllib.error.HTTPError as exc:
        return None, f"Tailscale API {method} {path} failed (HTTP {exc.code})."
    except urllib.error.URLError:
        return None, f"Could not reach the Tailscale API ({method} {path})."
    except Exception as exc:
        return None, f"Unexpected error calling the Tailscale API ({method} {path}): {type(exc).__name__}."


def _tailscale_api_call_with_retry(method, path, token, body=None, retries=TAILSCALE_API_RETRIES):
    """Retries _tailscale_api_call a small number of times on failure -
    covers transient network/API blips without ever looping forever or
    repeatedly issuing destructive calls (retries are just re-GETs/re-
    DELETEs of the same request, never a delete-then-recreate cycle)."""
    err = None
    for attempt in range(retries + 1):
        data, err = _tailscale_api_call(method, path, token, body=body)
        if err is None:
            return data, None
        if attempt < retries:
            time.sleep(TAILSCALE_API_RETRY_DELAY)
    return None, err


def list_tailnet_devices(token):
    """Returns (devices_list_or_None, error) via GET
    /tailnet/:tailnet/devices, using Config.TAILSCALE_TAILNET."""
    tailnet = Config.TAILSCALE_TAILNET
    if not tailnet:
        return None, "Tailscale tailnet is not configured (TAILSCALE_TAILNET)."
    path = f"/tailnet/{urllib.parse.quote(tailnet, safe='')}/devices"
    data, err = _tailscale_api_call_with_retry("GET", path, token)
    if err:
        return None, err
    devices = (data or {}).get("devices")
    if devices is None:
        return None, "Tailscale API response did not include a devices list."
    return devices, None


def _parse_tailscale_timestamp(value):
    """Parses a Tailscale API timestamp (RFC3339, e.g.
    '2026-08-23T11:09:28Z' or with fractional seconds) into a UTC
    datetime. Returns None on anything unparseable rather than raising -
    callers must treat that as "unknown, don't assume stale"."""
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def find_duplicate_device_via_api(hostname, current_device, token):
    """Returns (duplicates_list, error). `duplicates_list` is always a
    list (possibly empty) of devices that are safe to delete - never a
    single ambiguous guess. Applies every safety check from the project
    brief before including a device:
      - the current device's own identity must already be known (passed
        in, established from local `tailscale status`, never guessed)
      - a candidate must have a different device ID from the current one
      - a candidate must actually have the conflicting hostname
      - a candidate is only included if it hasn't been seen in the last
        TAILSCALE_STALE_LASTSEEN_SECONDS (or has no lastSeen at all,
        e.g. it never finished registering) - i.e. it's provably not the
        thing actively holding the name right now
    If ANY same-hostname candidate looks recently active (seen within
    that window), or its lastSeen can't be parsed at all, this refuses
    the WHOLE batch rather than guessing which ones are safe - it's only
    confident in "these are all long-dead", never in "pick one of these
    live-looking ones". This intentionally allows multiple dead orphans
    at once: repeatedly re-imaging the target machine (e.g. testing)
    leaves one dead node per rebuild, all self-reporting the same
    hostname - deleting all of them is correct, not ambiguous, as long
    as none of them looks alive."""
    if not current_device or not current_device.get("id"):
        return [], "Current device identity is not established; refusing to look for duplicates."

    devices, err = list_tailnet_devices(token)
    if err:
        return [], err

    target = hostname.strip().lower()
    current_id = current_device["id"]
    candidates = []
    for dev in devices:
        dev_id = dev.get("nodeId") or dev.get("id")
        dev_hostname = (dev.get("hostname") or (dev.get("name") or "").split(".")[0]).strip().lower()
        if not dev_id or dev_id == current_id:
            continue  # this is the current device (or unidentifiable) - never a candidate
        if dev_hostname != target:
            continue
        candidates.append(dev)

    if not candidates:
        return [], None

    now = datetime.datetime.now(datetime.timezone.utc)
    live_looking = []
    stale = []
    for dev in candidates:
        last_seen = _parse_tailscale_timestamp(dev.get("lastSeen"))
        if last_seen is None or (now - last_seen).total_seconds() < TAILSCALE_STALE_LASTSEEN_SECONDS:
            live_looking.append(dev)
        else:
            stale.append(dev)

    if live_looking:
        return [], (
            f"Found {len(candidates)} other device(s) named {hostname}, "
            f"{len(live_looking)} of which looks recently active; resolve manually in the admin console."
        )

    return stale, None


def remove_tailscale_device_via_api(device_id, token):
    """Deletes exactly one device via DELETE /device/:deviceID. Returns
    (ok, error). Caller (perform_tailscale_repair) is responsible for
    having already verified this device_id is not the current device."""
    _, err = _tailscale_api_call_with_retry("DELETE", f"/device/{device_id}", token, retries=1)
    return err is None, err


def _start_tailscale_up_async():
    """Kicks off `tailscale up` and returns immediately without waiting
    for it to finish - this is what actually triggers a reconnect and
    (if needed) opens the browser sign-in page. Deliberately NOT run
    through _run_tailscale/subprocess.run: `tailscale up` can block for
    as long as the user takes to sign in, and a timeout-and-kill (which
    _run_tailscale would do) could tear it down mid-auth. Callers should
    poll get_tailscale_backend_state() separately to see what actually
    happened. Returns (ok, error_or_None); never raises."""
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        subprocess.Popen(
            ["tailscale", "up"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs,
        )
        return True, None
    except FileNotFoundError:
        return False, "tailscale is not installed or not on PATH"
    except Exception as exc:
        return False, f"could not start tailscale up: {exc}"


def _run_shell_command(command, timeout):
    """Runs a full shell command LINE (e.g. the string
    _tailscale_install_command() returns - pipes, winget args and all)
    and returns (ok, output). Same never-raises contract as
    _run_tailscale, but uses shell=True since these are command lines,
    not argv lists."""
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout, **kwargs,
        )
    except subprocess.TimeoutExpired:
        return False, f"command timed out after {timeout}s"
    except Exception as exc:
        return False, f"command failed: {exc}"

    if result.returncode != 0:
        reason = (result.stderr or result.stdout or "non-zero exit").strip()
        return False, reason
    return True, result.stdout.strip()


def check_internet_reachable():
    """Standard low-level reachability probe: open a TCP connection to a
    well-known, effectively-always-up host:port (Google public DNS on
    port 53) rather than doing an HTTP request - cheaper, doesn't depend
    on any particular website being up, and doesn't need a URL parser.
    Connecting and immediately closing is enough; nothing is sent."""
    try:
        with socket.create_connection((INTERNET_CHECK_HOST, INTERNET_CHECK_PORT), timeout=INTERNET_CHECK_TIMEOUT):
            return True
    except OSError:
        return False


def _refresh_path_from_registry():
    """Windows-only. After winget installs something, it updates PATH in
    the registry (HKLM\\...\\Environment and HKCU\\Environment), but this
    already-running process keeps whatever PATH it started with - only
    *new* processes launched after the change pick it up. Without this,
    is_tailscale_installed()'s `tailscale version` call can fail right
    after a genuinely successful install simply because the freshly
    installed folder isn't on this process's PATH yet.
    Best-effort only: never raises, silently no-ops on non-Windows or if
    anything about the registry read goes wrong."""
    if sys.platform != "win32":
        return
    try:
        import winreg

        def read_path(root, subkey):
            with winreg.OpenKey(root, subkey) as key:
                value, _ = winreg.QueryValueEx(key, "Path")
                return value

        machine_path = read_path(winreg.HKEY_LOCAL_MACHINE,
                                  r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
        try:
            user_path = read_path(winreg.HKEY_CURRENT_USER, r"Environment")
        except OSError:
            user_path = ""
        combined = ";".join(p for p in (machine_path, user_path) if p)
        if combined:
            os.environ["PATH"] = combined
    except Exception:
        pass


def _tailscale_install_command():
    """Returns a shell command line (as a single string, since it's
    always run through a real shell/terminal - see
    _open_terminal_with_script) that installs Tailscale using each
    platform's own official method. None if there's no known unattended
    path for the current platform (troubleshoot flow falls back to just
    opening the download page in that case).

    Windows: winget is bundled with Windows 10 2004+/Windows 11 and is
    Microsoft's own official package manager - this is the same install
    method Tailscale's own docs recommend for scripted installs.
    macOS: Tailscale's official install script (used by their own docs)
    detects macOS and directs to the Mac App Store / pkg as appropriate;
    curl+sh is Tailscale's own documented one-liner, not something
    invented here.
    Linux: Tailscale's official install script, documented at
    https://tailscale.com/install.sh - detects the distro and uses its
    native package manager.
    """
    if sys.platform == "win32":
        return "winget install --id Tailscale.Tailscale -e --accept-source-agreements --accept-package-agreements"
    if sys.platform == "darwin":
        return "curl -fsSL https://tailscale.com/install.sh | sh"
    return "curl -fsSL https://tailscale.com/install.sh | sh"


def _open_terminal_with_script(script_lines, title="Al-Qemma - Tailscale"):
    """Opens a REAL, visible native terminal window running `script_lines`
    (a list of shell commands, run in order) - deliberately visible
    rather than run silently in the background, since this is only ever
    used for the user-initiated "troubleshoot" flow (installing software
    and/or `tailscale up`'s browser sign-in are both things the user
    should watch happen and be able to respond to, e.g. a UAC prompt or a
    sudo password).

    Writes a small temp script file and hands it to the platform's
    terminal, since that's far more reliable across platforms than trying
    to pass a long inline command string through each shell's own
    argument-quoting rules.

    Returns (ok, message). Never raises.
    """
    import tempfile

    try:
        if sys.platform == "win32":
            fd, path = tempfile.mkstemp(suffix=".bat")
            with os.fdopen(fd, "w") as f:
                f.write("@echo off\r\n")
                f.write(f"title {title}\r\n")
                for line in script_lines:
                    f.write(line + "\r\n")
                f.write("echo.\r\n")
                f.write("pause\r\n")
            # A new, visible console window running the script.
            subprocess.Popen(["cmd.exe", "/c", "start", "", "cmd.exe", "/k", path])
            return True, None

        if sys.platform == "darwin":
            fd, path = tempfile.mkstemp(suffix=".sh")
            with os.fdopen(fd, "w") as f:
                f.write("#!/bin/sh\n")
                for line in script_lines:
                    f.write(line + "\n")
                f.write('echo; read -p "Press Enter to close..." _\n')
            os.chmod(path, 0o755)
            # Terminal.app running the script - `open -a Terminal` is the
            # standard way to hand a script to the user's default
            # terminal on macOS.
            subprocess.Popen(["open", "-a", "Terminal", path])
            return True, None

        # Linux: no single standard terminal binary - try common ones in
        # order, same fallback approach most cross-platform desktop apps
        # use since there's no portable "open a terminal" API.
        fd, path = tempfile.mkstemp(suffix=".sh")
        with os.fdopen(fd, "w") as f:
            f.write("#!/bin/sh\n")
            for line in script_lines:
                f.write(line + "\n")
            f.write('echo; read -p "Press Enter to close..." _\n')
        os.chmod(path, 0o755)

        for terminal_cmd in (
            ["x-terminal-emulator", "-e", path],
            ["gnome-terminal", "--", path],
            ["konsole", "-e", path],
            ["xterm", "-e", path],
        ):
            try:
                subprocess.Popen(terminal_cmd)
                return True, None
            except FileNotFoundError:
                continue
        return False, "No supported terminal emulator was found on this system."

    except Exception as exc:
        return False, f"Could not open a terminal: {exc}"


def _open_log_tail_terminal(log_path, title="Al-Qemma - Tailscale Repair"):
    """Opens a real, visible terminal that just tails `log_path` live.

    The actual repair logic (perform_tailscale_repair, below) runs
    in-process in a background Python thread and writes its step-by-step
    output to `log_path` as it goes - that's what makes the hostname and
    duplicate-device checks practical (they need `tailscale status
    --json` parsed as JSON, which plain batch/shell can't do cleanly).
    This function just gives the user a real terminal window to watch it
    happen in, reusing _open_terminal_with_script exactly as the old
    install+login script did - only the one command it runs is
    different. `log_path` must already exist (even empty) before this is
    called; Get-Content -Wait / tail -f both expect that.
    """
    if sys.platform == "win32":
        ps_path = log_path.replace("'", "''")
        script_lines = [f"powershell -NoExit -NoProfile -Command \"Get-Content -Path '{ps_path}' -Wait\""]
    else:
        sh_path = log_path.replace('"', '\\"')
        script_lines = [f'tail -f -n +1 "{sh_path}"']
    return _open_terminal_with_script(script_lines, title=title)


def _repair_log_writer(log_path):
    """Returns a write(line="") closure that appends `line` + a newline
    to `log_path`, flushing to disk immediately so the terminal tailing
    it (see _open_log_tail_terminal) picks it up right away. Never
    raises - if the log file can't be written, the repair keeps running
    regardless, it just won't be visible."""
    def write(line=""):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
    return write


def _detect_and_clean_hostname_conflict(hostname, self_id, log):
    """One detect -> clean-up pass: looks for another device already
    using `hostname` and, if the Tailscale API is configured, removes
    any that are confirmed stale duplicates (see
    find_duplicate_device_via_api's safety checks). Returns
    conflict_or_None - None means `hostname` is currently free to
    claim; a dict means it's still taken and perform_tailscale_repair's
    caller should not attempt `tailscale set --hostname=` yet.

    Pulled out of perform_tailscale_repair() so it can be re-run as
    part of a retry loop there instead of only once per repair run -
    see TAILSCALE_HOSTNAME_CLAIM_ATTEMPTS for why a single pass isn't
    reliable enough on its own."""
    conflict, err = find_conflicting_tailscale_device(hostname, self_id=self_id)
    if err:
        log(f"      [WARN] Could not check for conflicts: {err}")
        return conflict
    if not conflict:
        log(f"      [OK] No other device is using {hostname}.")
        return None

    log(f"      [WARN] Another device is already using {hostname}:")
    log(f"          Name:      {conflict.get('DNSName') or conflict.get('HostName')}")
    log(f"          Last seen: {conflict.get('LastSeen', 'unknown')}")
    log(f"          Online:    {conflict.get('Online', 'unknown')}")

    # current_device is built from local `tailscale status` (self_id,
    # already established by the caller) - never from the API - so the
    # "current device" identity is trusted before any API call is made,
    # per the safety requirement.
    self_info, _, _ = get_tailscale_status_full()
    current_hostname = (self_info or {}).get("HostName", "")
    current_device = {"id": self_id, "hostname": current_hostname}

    if not (Config.TAILSCALE_OAUTH_CLIENT_ID and Config.TAILSCALE_OAUTH_CLIENT_SECRET):
        log("      [ERROR] Tailscale OAuth credentials are not configured.")
        log("      This app will not remove devices without successful API authentication.")
        log("      Remove or rename it in the admin console, then click Fix Problems again:")
        log(f"          {TAILSCALE_ADMIN_MACHINES_URL}")
        return conflict
    if not self_id:
        log("      [FAILED] Current device identity is not established - refusing to remove anything.")
        return conflict

    token, token_err = _get_tailscale_oauth_token()
    if token_err:
        log(f"      [ERROR] Unable to authenticate with Tailscale API: {token_err}")
        log("      No device removed.")
        return conflict

    log("      Verifying device identity via the Tailscale API...")
    duplicates, dup_err = find_duplicate_device_via_api(hostname, current_device, token)
    if dup_err:
        log(f"      [WARN] {dup_err}")
        log("      No device removed - see the message above.")
        return conflict
    if not duplicates:
        log("      [OK] API found no confirmed duplicate (conflict may have just cleared).")
        return None

    if len(duplicates) > 1:
        log(f"      Found {len(duplicates)} stale devices all using {hostname} (likely left over from previous rebuilds/reinstalls of this machine) - all confirmed not the current device and not recently active.")
    remaining_ids = []
    for duplicate in duplicates:
        dup_id = duplicate.get("nodeId") or duplicate.get("id")
        log(f"      Verifying device identity... confirmed this is NOT the current device (id={dup_id}).")
        log(f"      Removing stale conflicting device (id={dup_id}) because it conflicts with the required {hostname} identity...")
        removed_ok, remove_err = remove_tailscale_device_via_api(dup_id, token)
        if not removed_ok:
            log(f"      [FAILED] Could not remove the conflicting device: {remove_err}")
            remaining_ids.append(dup_id)
        else:
            log("      [OK] Device removed.")

    if remaining_ids:
        log(f"      [WARN] {len(remaining_ids)} conflicting device(s) could not be removed - {hostname} may still conflict.")
        return conflict

    # Don't trust the deletes alone - re-query and only treat the name
    # as free once the Tailnet itself confirms every removed device is
    # actually gone, retrying once if any is still listed right away.
    removed_ids = {duplicate.get("nodeId") or duplicate.get("id") for duplicate in duplicates}
    time.sleep(TAILSCALE_POST_DELETE_SETTLE)
    devices_after, _ = list_tailnet_devices(token)
    still_listed = removed_ids & {(d.get("nodeId") or d.get("id")) for d in (devices_after or [])}
    if still_listed:
        log("      Still listed immediately after removal - waiting and re-checking...")
        time.sleep(TAILSCALE_POST_DELETE_SETTLE)
        devices_after, _ = list_tailnet_devices(token)
        still_listed = removed_ids & {(d.get("nodeId") or d.get("id")) for d in (devices_after or [])}
    if still_listed:
        log(f"      [WARN] {len(still_listed)} device(s) still listed after removal - {hostname} may still conflict.")
        return conflict

    log(f"      [OK] {hostname} is now free.")
    return None


def perform_tailscale_repair(hostname, log):
    """Runs the full Tailscale repair flow end-to-end, reporting each
    step through log(line) as it goes. Every step queries real Tailscale
    state before deciding what to print or do - it never claims to be
    doing something (e.g. "signing in") that isn't actually true, which
    was the original terminal output's main problem.

    Returns {"ok": bool, "message": str, "conflict": peer_dict_or_None}.
    `ok` is only True if the device ends the run installed, running,
    logged in, using `hostname`, and free of a hostname conflict.
    Never raises - every Tailscale call this uses is already
    individually exception-safe (see _run_tailscale/_start_tailscale_up_async).
    """
    log("=" * 40)
    log("      Al-Qemma Tailscale Repair")
    log("=" * 40)
    log("")

    # ---- [1/6] Installation ----
    log("[1/6] Checking Tailscale installation...")
    if is_tailscale_installed():
        log("      [OK] Tailscale is installed.")
    else:
        log("      [!] Tailscale is not installed - installing now (this can take a few minutes)...")

        # On a brand-new Windows machine this is often the very first
        # thing that needs internet access - which a freshly-imaged shop
        # PC frequently doesn't have yet. Try the bundled offline
        # installer first (no network, no winget required at all); only
        # fall back to the winget/curl one-liner when it's not present
        # (dev machine, or the vendor file wasn't added before building
        # - see BUILD_EXE.md) or it doesn't leave `tailscale` on PATH.
        installed_via_bundle = False
        if sys.platform == "win32" and os.path.isfile(TAILSCALE_OFFLINE_INSTALLER_PATH):
            log(f"      Found bundled installer - running it silently...")
            try:
                # /quiet is Tailscale's documented silent-install switch
                # for their Windows .exe installer as of this writing -
                # worth double-checking against
                # https://tailscale.com/kb/1080/cli before relying on it
                # if you're bundling a much newer/older installer build,
                # since installer command-line behavior can change
                # between releases.
                subprocess.run(
                    [TAILSCALE_OFFLINE_INSTALLER_PATH, "/quiet"],
                    capture_output=True, timeout=TAILSCALE_INSTALL_TIMEOUT,
                )
            except Exception as exc:
                log(f"      [WARN] Bundled installer did not complete cleanly: {exc}")
            _refresh_path_from_registry()
            installed_via_bundle = is_tailscale_installed()
            if installed_via_bundle:
                log("      [OK] Tailscale installed successfully from the bundled installer.")

        if not installed_via_bundle:
            install_cmd = _tailscale_install_command()
            ok, output = _run_shell_command(install_cmd, timeout=TAILSCALE_INSTALL_TIMEOUT)
            _refresh_path_from_registry()
            if not is_tailscale_installed():
                log(f"      [FAILED] Could not install Tailscale: {output}")
                log("")
                log("Please install Tailscale manually from https://tailscale.com/download and try again.")
                return {"ok": False, "message": "Tailscale installation failed.", "conflict": None}
            if not ok:
                log(f"      [WARN] Installer exited non-zero but Tailscale is present - continuing. (installer output: {output.splitlines()[-1] if output else 'n/a'})")
            log("      [OK] Tailscale installed successfully.")
    log("")

    # ---- [2/6] Service + [3/6] Login - both driven by `tailscale up`,
    # so they're checked together and only actually run it once. ----
    log("[2/6] Checking Tailscale service...")
    state, err = get_tailscale_backend_state()
    already_running = state == "Running"
    if already_running:
        log("      [OK] Tailscale is running.")
        log("")
        log("[3/6] Checking Tailscale login...")
        log("      [OK] Already logged in.")
    else:
        needs_login = state == "NeedsLogin"
        log(f"      Tailscale is not running yet (state: {state or err}) - starting it...")
        ok, start_err = _start_tailscale_up_async()
        if not ok:
            log(f"      [FAILED] {start_err}")
            return {"ok": False, "message": start_err, "conflict": None}
        log("")
        log("[3/6] Checking Tailscale login...")
        if needs_login:
            log("      Not logged in - opening the sign-in page in your browser...")
        log("      Waiting for Tailscale to connect...")

        deadline = time.time() + TAILSCALE_LOGIN_WAIT_TIMEOUT
        announced_login = needs_login
        while time.time() < deadline:
            time.sleep(2)
            state, err = get_tailscale_backend_state()
            if state == "Running":
                break
            if state == "NeedsLogin" and not announced_login:
                log("      Sign-in required - opening the sign-in page in your browser...")
                announced_login = True
        if state != "Running":
            log(f"      [FAILED] Still not connected ({state or err}).")
            log("")
            log("Finish signing in in the browser, then click Fix Problems again.")
            return {"ok": False, "message": "Waiting for Tailscale sign-in.", "conflict": None}
        log("      [OK] Connected and logged in.")
    log("")

    # ---- [4/6] + [5/6] Conflict cleanup + hostname claim ----
    # This whole detect -> clean -> claim -> verify sequence has to run
    # as a unit, and be retried as a unit, not just attempted once: if
    # another device is still using `hostname` at the moment `tailscale
    # set --hostname=` runs, Tailscale doesn't reject the request - it
    # silently appends "-1" (then "-2", etc.) to make it unique, so the
    # name actually assigned is never the plain `hostname` this app
    # depends on for a fixed link. And even right after the API confirms
    # a duplicate device is deleted, Tailscale's control-plane name index
    # can take a moment longer to actually release it, so a single
    # detect-then-claim pass can still lose that race. See
    # TAILSCALE_HOSTNAME_CLAIM_ATTEMPTS.
    self_info, _, err = get_tailscale_status_full()
    if self_info is None:
        log(f"      [FAILED] Could not read Tailscale status: {err}")
        return {"ok": False, "message": err, "conflict": None}
    current_hostname = self_info.get("HostName", "")
    self_id = self_info.get("ID")

    conflict = None
    for attempt in range(1, TAILSCALE_HOSTNAME_CLAIM_ATTEMPTS + 1):
        suffix = "" if attempt == 1 else f", retry {attempt - 1}"

        log(f"[4/6{suffix}] Checking Tailnet for a conflicting device...")
        conflict = _detect_and_clean_hostname_conflict(hostname, self_id, log)
        log("")

        log(f"[5/6{suffix}] Setting device hostname...")
        if current_hostname.lower() == hostname.lower():
            log(f"      [OK] Hostname is already {hostname}.")
            log("")
            break

        log(f"      Current hostname is '{current_hostname or 'unknown'}' - setting it to {hostname}...")
        ok, output = set_tailscale_hostname(hostname)
        if not ok:
            log(f"      [FAILED] Could not set hostname: {output}")
            return {"ok": False, "message": f"Could not set hostname: {output}", "conflict": conflict}

        self_info, _, err = get_tailscale_status_full()
        current_hostname = (self_info or {}).get("HostName", "")
        if current_hostname.lower() == hostname.lower():
            log(f"      [OK] Hostname set to {hostname}.")
            conflict = None
            log("")
            break

        log(f"      [WARN] Tailscale assigned '{current_hostname}' instead - {hostname} is already taken.")
        conflict = conflict or {"HostName": current_hostname}
        if attempt < TAILSCALE_HOSTNAME_CLAIM_ATTEMPTS:
            log(f"      Waiting for the name to fully release, then trying again ({attempt}/{TAILSCALE_HOSTNAME_CLAIM_ATTEMPTS})...")
            time.sleep(TAILSCALE_POST_DELETE_SETTLE)
        log("")

    # ---- Extra safety sweep (a SECOND, independent layer of protection
    # on top of the claim loop above) ----
    # Only runs once the hostname has actually been claimed
    # successfully. Re-scans the Tailnet a few more times for any OTHER
    # device that has since claimed/re-claimed our hostname, deleting
    # it the exact same way as the main loop above - this catches a
    # duplicate that surfaces slightly late (API eventual consistency,
    # or another one of this same machine's old orphaned registrations
    # showing up after the fact) instead of only ever being caught the
    # next time someone happens to manually rerun this repair.
    if conflict is None and current_hostname.lower() == hostname.lower():
        for sweep_pass in range(1, TAILSCALE_POST_SUCCESS_SWEEP_PASSES + 1):
            log(f"[extra check {sweep_pass}/{TAILSCALE_POST_SUCCESS_SWEEP_PASSES}] Re-scanning for a duplicate device after success...")
            sweep_conflict = _detect_and_clean_hostname_conflict(hostname, self_id, log)
            log("")
            if sweep_conflict is not None:
                # Something re-claimed the name after we set it -
                # surface this as a real conflict for [6/6] below to
                # report, exactly as the main loop would have.
                conflict = sweep_conflict
                break
            if sweep_pass < TAILSCALE_POST_SUCCESS_SWEEP_PASSES:
                time.sleep(TAILSCALE_POST_DELETE_SETTLE)

    # ---- [6/6] Final verification ----
    log("[6/6] Final verification...")
    installed_ok = is_tailscale_installed()
    state, _ = get_tailscale_backend_state()
    running_ok = state == "Running"
    self_info, _, _ = get_tailscale_status_full()
    final_hostname = (self_info or {}).get("HostName", "")
    hostname_ok = final_hostname.lower() == hostname.lower()

    for ok, label in (
        (installed_ok, "Tailscale installed"),
        (running_ok, "Tailscale connected"),
        (running_ok, "Account authenticated"),
        (hostname_ok, f"Hostname: {final_hostname or 'unknown'}"),
        (not conflict, "No conflicting device"),
    ):
        log(f"      [{'OK' if ok else 'FAILED'}] {label}")
    log("")

    all_ok = installed_ok and running_ok and hostname_ok and not conflict
    log("=" * 40)
    if all_ok:
        log("              COMPLETE")
        log("=" * 40)
        log("")
        log("Tailscale is ready.")
        message = "Tailscale is ready."
    else:
        log("            ACTION NEEDED")
        log("=" * 40)
        log("")
        if conflict:
            log(f"Hostname could not be set to {hostname} - another device is using it.")
            message = f"Another device is already using {hostname}."
        else:
            log("Tailscale is not fully configured yet - see the details above.")
            message = "Tailscale needs attention - see the terminal window."

    return {"ok": bool(all_ok), "message": message, "conflict": conflict}


# ============================================================
# Server controller
# ============================================================
class ServerController:
    """Owns Flask app creation and the Waitress serve thread. No network
    detection (LAN/Tailscale) in this phase - that's Phases 5 and 6.

    Uses waitress's lower-level create_server() (instead of the one-shot
    serve() helper) so stop() can close the listening socket for a clean
    shutdown - used by AppAPI.exit_application() below.
    """

    def __init__(self, host=HOST, port=PORT):
        self.host = host
        self.port = port
        self.app = None
        self.thread = None
        self._wsgi_server = None
        self.local_url = f"http://127.0.0.1:{port}/login"

        self._network_lock = threading.Lock()
        self._network_info = {
            "lan_ip": None,
            "tailscale_ip": None,
            "tailscale_dns_name": None,
            "tailscale_serve_ok": False,
        }
        self._connectivity_lock = threading.Lock()
        self._connectivity = {
            "status": "checking",   # "tailscale" | "no_tailscale" | "offline" | "checking"
            "message": "جارٍ التحقق من الاتصال...",
        }

    def create_app(self):
        from app import create_app
        self.app = create_app()
        # Lets a Flask route (see app/routes/connectivity.py) read the same
        # cached status this class already pushes to the pywebview window
        # via window.evaluate_js - needed so plain-browser access (LAN/
        # Tailscale URL opened in a normal browser tab, not the desktop
        # app) can poll for it over HTTP instead, since evaluate_js only
        # reaches the native window and has no effect on a separate browser.
        self.app.config["SERVER_CONTROLLER"] = self
        return self.app

    def start(self, log_callback=None):
        """Starts the Flask server in a background thread. Raises whatever
        create_app() raises so the caller can show a startup error."""
        if self.app is None:
            self.create_app()

        from waitress.server import create_server
        self._wsgi_server = create_server(self.app, host=self.host, port=self.port)

        def _serve():
            try:
                self._wsgi_server.run()
            except Exception as exc:
                if log_callback:
                    log_callback(f"Server stopped unexpectedly: {exc}", level="error")

        self.thread = threading.Thread(target=_serve, daemon=True)
        self.thread.start()

    def stop(self):
        if self._wsgi_server is not None:
            try:
                self._wsgi_server.close()
            except Exception:
                pass

    # ---- Network detection (LAN + Tailscale) ----
    def start_network_detection(self, logger):
        """Kicks off detect_network() in a daemon background thread and
        returns immediately - never blocks the caller. Safe to call
        multiple times; each call just re-detects."""
        thread = threading.Thread(target=self._detect_network, args=(logger,), daemon=True)
        thread.start()

    def _detect_network(self, logger):
        """Runs entirely off the main/GUI thread. Every step here is
        already individually exception-safe (get_lan_ip and the tailscale
        helpers never raise), but this is wrapped in a blanket try/except
        too - a background thread dying silently is fine, a background
        thread taking the process down with it is not."""
        try:
            lan_ip = get_lan_ip()
            with self._network_lock:
                self._network_info["lan_ip"] = lan_ip
            logger.info(f"LAN IP detected: {lan_ip}" if lan_ip else "LAN IP detection failed.")

            self._refresh_tailscale_network_info(logger)
        except Exception as exc:
            logger.error(f"Network detection failed unexpectedly: {exc}")

    def _refresh_tailscale_network_info(self, logger):
        """Re-detects tailscale_ip/dns_name/serve_ok and updates
        _network_info. Same detection _detect_network() does once at
        startup, but callable again later - used by the connectivity
        monitor below so the "الرابط العام (Tailscale)" field actually
        picks up Tailscale once it comes online, instead of staying
        empty forever if it wasn't ready in the first few seconds after
        launch."""
        tailscale_ip, ts_err = get_tailscale_ip()
        if not tailscale_ip:
            logger.info(f"Tailscale not available: {ts_err}")
            return
        with self._network_lock:
            self._network_info["tailscale_ip"] = tailscale_ip
        logger.info(f"Tailscale IP detected: {tailscale_ip}")

        dns_name, dns_err = get_tailscale_dns_name()
        if dns_name:
            with self._network_lock:
                self._network_info["tailscale_dns_name"] = dns_name
            logger.info(f"Tailscale DNS name: {dns_name}")
        else:
            logger.info(f"Tailscale DNS name unavailable: {dns_err}")

        serve_ok, serve_output = start_tailscale_serve(self.port)
        with self._network_lock:
            self._network_info["tailscale_serve_ok"] = serve_ok
        if serve_ok:
            logger.info("tailscale serve started.")
        else:
            logger.info(f"tailscale serve not started: {serve_output}")

    def refresh_tailscale_network_info(self, logger):
        """Public wrapper around _refresh_tailscale_network_info() - lets
        callers outside this class (AppAPI.troubleshoot_tailscale, after
        a repair) force a re-detect instead of waiting for the next
        connectivity-monitor tick to notice a hostname/link change."""
        self._refresh_tailscale_network_info(logger)

    def get_urls(self):
        """Thread-safe snapshot -> {'lan_url': ..., 'public_url': ...},
        either value None if not (yet) available. Safe to call at any
        time, including before detection has finished - callers just see
        None until the background thread fills it in."""
        with self._network_lock:
            info = dict(self._network_info)

        lan_url = f"http://{info['lan_ip']}:{self.port}/login" if info["lan_ip"] else None

        if info["tailscale_serve_ok"] and info["tailscale_dns_name"]:
            public_url = f"https://{info['tailscale_dns_name']}/login"
        elif info["tailscale_ip"]:
            public_url = f"http://{info['tailscale_ip']}:{self.port}/login"
        else:
            public_url = None

        return {"lan_url": lan_url, "public_url": public_url}

    # ---- Background connectivity monitor (internet + Tailscale) ----
    def start_connectivity_monitor(self, logger, window=None):
        """Kicks off a daemon thread that re-checks internet reachability
        + Tailscale status every CONNECTIVITY_CHECK_INTERVAL seconds, for
        as long as the app runs - separate from start_network_detection()
        above, which only ever runs once at startup to find LAN/Tailscale
        URLs. `window` is optional so this can also run under the
        headless console launcher (which has no pywebview window to push
        JS into - it still logs status there, just skips evaluate_js)."""
        thread = threading.Thread(
            target=self._connectivity_loop, args=(logger, window), daemon=True
        )
        thread.start()

    def _notify_connectivity_change(self, event_type, title, message):
        """Runs notify_event() for a Tailscale connect/disconnect edge.
        This is a background thread, not a Flask request, so it needs its
        own app_context to use current_app-dependent things inside
        notify_event() (config, logger). Imported lazily - importing
        app.routes.notifications at module load time would import the
        whole Flask app package before create_app() has run."""
        if self.app is None:
            return
        try:
            from app.routes.notifications import notify_event
            with self.app.app_context():
                notify_event(event_type, title, message, url="/settings/")
        except Exception as exc:
            self.app.logger.info(f"Could not send connectivity notification: {exc}") if self.app else None

    def _check_connectivity_once(self):
        """One check cycle -> (status, message). Never raises - every
        failure mode (no internet, tailscale missing, tailscale installed
        but not logged in, subprocess errors) is caught and turned into a
        status string instead of propagating, since this runs unattended
        in a loop with nothing watching for exceptions."""
        try:
            if not check_internet_reachable():
                return "offline", "لا يوجد اتصال بالإنترنت."

            if not is_tailscale_installed():
                return "no_tailscale", "متصل بالإنترنت - Tailscale غير مثبت."

            state, err = get_tailscale_backend_state()
            if state == "Running":
                return "tailscale", "متصل عبر Tailscale."
            if state:
                return "no_tailscale", f"متصل بالإنترنت - Tailscale غير متصل ({state})."
            return "no_tailscale", f"متصل بالإنترنت - تعذر التحقق من حالة Tailscale ({err})."
        except Exception as exc:
            # Belt-and-suspenders: every helper above is already
            # individually exception-safe, but this loop must never die,
            # so nothing here is allowed to escape uncaught.
            return "offline", f"تعذر التحقق من حالة الاتصال: {exc}"

    def _connectivity_loop(self, logger, window):
        prev_status = None
        while True:
            status, message = self._check_connectivity_once()
            with self._connectivity_lock:
                self._connectivity = {"status": status, "message": message}

            logger.info(f"Connectivity check: {status} - {message}")

            if status == "tailscale":
                with self._network_lock:
                    have_ip = bool(self._network_info["tailscale_ip"])
                if not have_ip:
                    # Tailscale just came online after the app already
                    # started (e.g. user ran `tailscale up` from the
                    # troubleshoot terminal) - the one-shot startup
                    # detection missed it, so re-detect now.
                    self._refresh_tailscale_network_info(logger)

            # Toast on an actual Tailscale connect/disconnect transition
            # only - never on the first check right after startup (that's
            # just discovering the initial state, not a change), and never
            # on every routine tick that stays the same.
            if self.app is not None and prev_status is not None and status != prev_status:
                if status == "tailscale":
                    self._notify_connectivity_change("tailscale_connected", "تم الاتصال بـ Tailscale", message)
                elif prev_status == "tailscale":
                    self._notify_connectivity_change("tailscale_disconnected", "توقف اتصال Tailscale", message)
            prev_status = status

            self._push_status_to_window(status, message, window, logger)

            time.sleep(CONNECTIVITY_CHECK_INTERVAL)

    def _push_status_to_window(self, status, message, window, logger=None):
        """Pushes an already-known status/message + the current
        public_url to `window` via window.setConnectivityStatus (see
        base.html) - updates the status dot next to the SQLite version
        in the navbar, and (settings.html) the "الرابط العام (Tailscale)"
        field. window.setConnectivityStatus is expected to no-op
        gracefully if it isn't defined yet (e.g. a page that hasn't
        loaded that script). No-op if there's no window; never raises -
        the window may not be ready yet, or may have just been destroyed
        (app closing), and this must never take a caller down with it."""
        if window is None:
            return
        try:
            public_url = self.get_urls()["public_url"]
            window.evaluate_js(
                f"window.setConnectivityStatus && "
                f"window.setConnectivityStatus({json.dumps(status)}, {json.dumps(message)}, {json.dumps(public_url)})"
            )
        except Exception as exc:
            if logger is not None:
                logger.info(f"Could not push connectivity status to the window: {exc}")

    def push_current_status_to_window(self, window, logger=None):
        """Re-checks connectivity right now, updates the cached snapshot
        get_connectivity_status() returns, and pushes it to `window`.
        Separate from the periodic _connectivity_loop above - this is
        for callers that just changed the Tailscale state themselves
        (AppAPI.troubleshoot_tailscale after a repair finishes) and want
        the Settings page to reflect it immediately, instead of waiting
        up to CONNECTIVITY_CHECK_INTERVAL seconds for the next tick."""
        status, message = self._check_connectivity_once()
        with self._connectivity_lock:
            self._connectivity = {"status": status, "message": message}
        self._push_status_to_window(status, message, window, logger)

    def get_connectivity_status(self):
        """Thread-safe snapshot for AppAPI.get_connectivity_status(), used
        by settings.html to show the current state immediately on load
        rather than waiting for the next 60-second tick."""
        with self._connectivity_lock:
            return dict(self._connectivity)


def wait_for_server(host, port, timeout=10.0):
    """Poll until something is listening on host:port, or give up after
    `timeout` seconds. Used instead of a fixed sleep so startup is as fast
    as the server actually allows."""
    deadline = time.time() + timeout
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    while time.time() < deadline:
        try:
            with socket.create_connection((probe_host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def graceful_shutdown(server, settings, backup_manager, logger, guard, reason=""):
    """Runs the backup (if auto_backup_on_close is enabled) and stops the
    Flask server. Shared by the in-app Exit button (AppAPI.exit_application)
    and the native OS close button (window.events.closing handler below) so
    there's exactly one shutdown path instead of two that can drift apart.

    `guard` is a shared, lock-protected {"done": False} dict (created once
    in main() and passed to both callers) - equivalent to the old file's
    self._shutting_down flag. It's needed because exit_application() calls
    window.destroy() at the end, which itself fires window.events.closing -
    i.e. this function's *other* caller - so without the guard, clicking
    the in-app Exit button would run the backup and server.stop() twice.

    Never raises - whichever caller invoked this still needs to actually
    close the window/process afterward regardless of backup outcome.
    """
    with guard["lock"]:
        if guard["done"]:
            logger.info(f"Shutdown already handled - skipping duplicate call ({reason}).")
            return
        guard["done"] = True

    logger.info(f"Shutting down ({reason}).")

    if settings.get("auto_backup_on_close", False):
        destination = settings.get("backup_destination") or DEFAULT_BACKUP_DIR
        ok, message = backup_manager.run_backup(destination)
        logger.info(message) if ok else logger.error(message)

    try:
        server.stop()
    except Exception as exc:
        logger.error(f"Error while stopping server: {exc}")


# ============================================================
# Silent OS printing (best-effort, never raises)
# ============================================================
def _print_file_silently(path, logger):
    """Sends a file straight to the OS's default printer, no dialog. If
    there's no printer configured, or the print attempt fails for any
    reason, this just logs it and returns - it never raises further and
    is never reported back to the UI either way. There's no reliable way
    from here to tell "no printer" apart from "printer misbehaved" apart
    from "user doesn't care", and the file the user explicitly chose to
    save (in print_receipt below) is the part that actually matters -
    printing on top of that is a bonus, not something worth interrupting
    the user over if it doesn't work."""
    try:
        if sys.platform == "win32":
            # ShellExecute's "print" verb hands the file to whatever
            # app is associated with .pdf and asks it to print silently
            # to the default printer - the standard no-dialog print
            # path on Windows.
            os.startfile(path, "print")
        else:
            # macOS and Linux both ship CUPS' `lp`, which prints
            # straight to the default printer with no dialog. If CUPS
            # isn't installed or there's no default printer, this just
            # exits non-zero - swallowed here, not raised.
            subprocess.run(
                ["lp", path], check=False, timeout=15,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except Exception as exc:
        logger.exception("_print_file_silently failed")


# ============================================================
# js_api bridge — everything under window.pywebview.api in settings.html
# ============================================================
class AppAPI:
    """Methods here are called directly from JS as
    window.pywebview.api.<method>(...). pywebview marshals args/return
    values through JSON, so keep both plain (str/int/bool/dict/list/None).

    Every method that isn't fully implemented yet returns an honest
    placeholder instead of raising - a real exception here surfaces to the
    settings page as a rejected promise, and .catch() isn't wired up
    everywhere in that JS, so failing loudly would misbehave silently
    rather than helpfully.
    """

    def __init__(self, window, server, settings, logger, shutdown_guard, backup_manager=None):
        self._window = window
        self._server = server
        self._settings = settings
        self._logger = logger
        self._shutdown_guard = shutdown_guard
        self._backup = backup_manager or BackupManager(logger=logger)

    # ---- Website Access + Backup panel ----
    def get_settings_data(self):
        data = self._settings.get_all()
        urls = self._server.get_urls()
        return {
            "local_url": self._server.local_url,
            "lan_url": urls["lan_url"],
            "public_url": urls["public_url"],
            "open_browser": bool(data.get("open_browser")),
            "auto_backup_on_close": bool(data.get("auto_backup_on_close")),
            "backup_destination": data.get("backup_destination") or DEFAULT_BACKUP_DIR,
            "window_width": data.get("window_width", DEFAULT_WINDOW_WIDTH),
            "window_height": data.get("window_height", DEFAULT_WINDOW_HEIGHT),
            "resizable": bool(data.get("resizable", True)),
        }

    def save_settings(self, auto_backup_on_close, backup_destination, open_browser):
        self._settings.set(
            auto_backup_on_close=bool(auto_backup_on_close),
            backup_destination=(backup_destination or "").strip(),
            open_browser=bool(open_browser),
        )
        self._logger.info("Settings saved (backup/website preferences).")

    # ---- Launch window size ----
    def save_window_settings(self, width, height, resizable):
        width, height = _clamp_window_size(width, height)
        self._settings.set(window_width=width, window_height=height, resizable=bool(resizable))
        self._logger.info(f"Window settings saved: {width}x{height}, resizable={bool(resizable)}")
        return {"width": width, "height": height, "resizable": bool(resizable)}

    # ---- Theme ----
    def get_theme(self):
        return self._settings.get("theme", "light")

    def save_theme(self, theme):
        theme = "dark" if theme == "dark" else "light"
        self._settings.set(theme=theme)
        return theme

    # ---- Terminal & Error Log Viewer (implemented for real - plain file
    #      read, no meaningful platform risk) ----
    def fetch_logs(self):
        try:
            with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            return "".join(lines[-FETCH_LOGS_MAX_LINES:]) or "(log is empty)"
        except FileNotFoundError:
            return "(no log file yet)"
        except OSError as exc:
            return f"(could not read log file: {exc})"

    # ---- Connectivity / Tailscale status + troubleshooting ----
    def get_connectivity_status(self):
        """Lets settings.html show the current dot state immediately on
        page load, instead of waiting up to 60s for the background
        monitor's next tick. Same {"status", "message"} shape the monitor
        pushes via evaluate_js."""
        return self._server.get_connectivity_status()

    def troubleshoot_tailscale(self):
        """The Settings page "حل المشكلة" (Fix Problems) button, for when
        the navbar dot isn't green.

        On Windows, this runs the full repair flow (perform_tailscale_repair):
        install if missing, connect/sign in if needed, set the fixed
        device hostname (TAILSCALE_HOSTNAME), check the tailnet for a
        conflicting device, and verify the end result - each step
        checking real Tailscale state first, so the terminal never
        claims to be doing something (like signing in) that isn't
        actually true. The repair itself runs in-process in a background
        thread (so it can parse `tailscale status --json` for the
        hostname/duplicate-device checks, which plain batch can't do),
        while a real terminal window tails its log output live so the
        user can watch it happen - same visible-terminal UX as before,
        just backed by Python instead of a hand-rolled .bat script. Once
        it finishes, the Settings page's Tailscale link is refreshed and
        pushed immediately (see ServerController.push_current_status_to_window),
        instead of waiting for the next periodic connectivity check.

        On macOS/Linux, this still uses the original install+sign-in
        terminal script (fixed hostname / duplicate-device handling
        aren't implemented on those platforms yet), just with the
        "Signing in" line only shown when the device is actually not
        logged in yet.
        """
        if sys.platform == "win32":
            return self._troubleshoot_tailscale_windows()
        return self._troubleshoot_tailscale_other()

    def _troubleshoot_tailscale_windows(self):
        import tempfile

        try:
            fd, log_path = tempfile.mkstemp(suffix=".log", prefix="alqemma_tailscale_")
            os.close(fd)
        except OSError as exc:
            self._logger.error(f"troubleshoot_tailscale: could not create log file: {exc}")
            return {"ok": False, "message": "تعذر إنشاء ملف السجل."}

        ok, message = _open_log_tail_terminal(log_path, title="Al-Qemma - Tailscale Repair")
        if not ok:
            self._logger.error(f"troubleshoot_tailscale: {message}")
            return {"ok": False, "message": message or "تعذر فتح نافذة الطرفية."}

        log = _repair_log_writer(log_path)

        def _run_repair():
            try:
                result = perform_tailscale_repair(TAILSCALE_HOSTNAME, log)
                self._logger.info(f"Tailscale repair finished: {result}")
            except Exception as exc:
                log(f"\n[FAILED] Unexpected error: {exc}")
                self._logger.error(f"Tailscale repair failed unexpectedly: {exc}")
            finally:
                # Re-read the (possibly now-different) Tailscale state and
                # push it to the Settings page immediately, rather than
                # waiting up to CONNECTIVITY_CHECK_INTERVAL seconds for
                # the periodic monitor to notice - this is what actually
                # fixes the "link doesn't update after repair" problem.
                try:
                    self._server.refresh_tailscale_network_info(self._logger)
                    self._server.push_current_status_to_window(self._window, self._logger)
                except Exception as exc:
                    self._logger.info(f"Could not refresh Tailscale status after repair: {exc}")

        threading.Thread(target=_run_repair, daemon=True).start()
        self._logger.info("Opened Tailscale repair terminal and started repair.")
        return {"ok": True, "message": "تم فتح نافذة الطرفية. تابع التقدم هناك."}

    def _troubleshoot_tailscale_other(self):
        """macOS/Linux fallback - same install+sign-in terminal script as
        before, but checks real login state first so it only shows the
        "Signing in" line when that's actually about to happen. Fixed
        hostname and duplicate-device handling are Windows-only for now
        (see troubleshoot_tailscale's docstring)."""
        install_cmd = _tailscale_install_command()
        installed = is_tailscale_installed()
        state, _ = get_tailscale_backend_state() if installed else (None, None)
        already_connected = state == "Running"

        script_lines = ["echo Checking Tailscale..."]
        if installed:
            script_lines.append("echo Tailscale is installed.")
        else:
            script_lines += [
                "echo Tailscale not found - installing...",
                f"{install_cmd}",
            ]
        if already_connected:
            script_lines += [
                "echo Already logged in - re-checking connection...",
                "sudo tailscale up",
            ]
        else:
            script_lines += [
                "echo Not logged in - opening the sign-in page in your browser...",
                "sudo tailscale up",
            ]

        ok, message = _open_terminal_with_script(script_lines, title="Al-Qemma - Tailscale Troubleshoot")
        if not ok:
            self._logger.error(f"troubleshoot_tailscale: {message}")
            return {"ok": False, "message": message or "تعذر فتح نافذة الطرفية."}

        self._logger.info("Opened Tailscale troubleshoot terminal.")
        return {"ok": True, "message": "تم فتح نافذة الطرفية. اتبع التعليمات هناك."}

    # ---- Backup & Exit ----
    def run_backup_now(self, destination):
        """Not currently wired to a button in settings.html (there isn't
        a standalone 'backup now' control - only auto-backup-on-close),
        but exposed on the bridge in case one gets added later without
        needing another phase just for this."""
        ok, message = self._backup.run_backup(destination)
        return {"ok": ok, "message": message}

    def exit_application(self):
        graceful_shutdown(self._server, self._settings, self._backup, self._logger, self._shutdown_guard, reason="Settings page Exit button")
        try:
            self._window.destroy()
        except Exception as exc:
            self._logger.error(f"Error while closing window: {exc}")

    # ---- Instance folder restore ----
    def restore_instance_from_folder(self):
        """Desktop counterpart to POST /settings/restore-instance (see
        app/routes/settings.py) - same replace-the-whole-instance-folder
        operation, just backed by a native folder-select dialog since
        Python already has direct filesystem access here, so there's no
        need to upload anything over HTTP."""
        try:
            result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception as exc:
            self._logger.error(f"restore_instance_from_folder: dialog failed: {exc}")
            return {"ok": False, "message": f"تعذر فتح نافذة الاختيار: {exc}"}

        if not result:
            return {"ok": False, "message": "تم الإلغاء."}
        folder = result[0] if isinstance(result, (list, tuple)) else result

        ok, message = self._backup.restore_instance(folder)
        return {"ok": ok, "message": message}

    # ---- Generic text file save (used by the settings-page JS for any
    #      "export to .txt" button, so each one doesn't need its own
    #      bespoke pywebview.api method) ----
    def save_text_file(self, filename, content):
        try:
            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                directory="",
                save_filename=filename,
                file_types=("Text files (*.txt)",),
            )
        except Exception as exc:
            self._logger.error(f"save_text_file: save dialog failed: {exc}")
            return {"ok": False, "message": f"تعذر فتح نافذة الحفظ: {exc}"}

        if not result:
            return {"ok": False, "message": "تم الإلغاء."}
        path = result[0] if isinstance(result, (list, tuple)) else result

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as exc:
            self._logger.error(f"save_text_file: write failed: {exc}")
            return {"ok": False, "message": f"فشل حفظ الملف: {exc}"}

        self._logger.info(f"Saved text file to {path}")
        return {"ok": True, "message": f"تم الحفظ في: {path}"}

    # ---- Category product export (txt) ----
    def export_category_products(self, category_id, category_name="products"):
        """Writes "<model name>: <price>EGP" per line, one per active
        product in the category, straight to disk via pywebview's native
        Save dialog - no Flask response involved, so there's no
        Content-Disposition header to forget and no way for this to
        render inline as a blank page instead of downloading."""
        from app.services import products as product_service

        try:
            items = product_service.list_products(category_id=category_id)
        except Exception as exc:
            self._logger.error(f"export_category_products: failed to load products: {exc}")
            return {"ok": False, "message": f"فشل تحميل المنتجات: {exc}"}

        if not items:
            return {"ok": False, "message": "لا توجد منتجات في هذا القسم."}

        lines = [f"{p['name']}: {p['selling_price']:.0f}EGP" for p in items]
        content = "\n".join(lines)

        safe_name = "".join(c for c in (category_name or "products") if c.isalnum() or c in " _-").strip()
        safe_name = safe_name or "products"

        try:
            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                directory="",
                save_filename=f"{safe_name}.txt",
                file_types=("Text files (*.txt)",),
            )
        except Exception as exc:
            self._logger.error(f"export_category_products: save dialog failed: {exc}")
            return {"ok": False, "message": f"تعذر فتح نافذة الحفظ: {exc}"}

        if not result:
            return {"ok": False, "message": "تم الإلغاء."}
        path = result[0] if isinstance(result, (list, tuple)) else result

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as exc:
            self._logger.error(f"export_category_products: write failed: {exc}")
            return {"ok": False, "message": f"فشل حفظ الملف: {exc}"}

        self._logger.info(f"Exported {len(items)} product(s) from category {category_id} to {path}")
        return {"ok": True, "message": f"تم الحفظ في: {path}"}

    # ---- Single product download (images + specs, as a .zip) ----
    def export_product_package(self, product_id):
        """Same fix as export_category_products/save_receipt above, applied
        to a single product's download button on product_detail.html: no
        Flask send_file() response, so no chance of the WebView showing a
        blank page instead of a save prompt. Hands pywebview's native Save
        dialog the same zip bytes (<product name>/images/<files>,
        <product name>/specs.txt) that the browser-mode download route
        (GET /products/<id>/download, app/routes/products.py) serves over
        HTTP - both call product_service.build_product_package_zip() so
        the two environments can never disagree about package contents.
        """
        from app.services import products as product_service

        try:
            with self._server.app.app_context():
                result = product_service.build_product_package_zip(product_id)
        except Exception as exc:
            self._logger.error(f"export_product_package: failed to build package for product {product_id}: {exc}")
            return {"ok": False, "message": f"فشل تحميل بيانات المنتج: {exc}"}

        if not result:
            return {"ok": False, "message": "المنتج غير موجود."}
        safe_name, content = result

        try:
            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                directory="",
                save_filename=f"{safe_name}.zip",
                file_types=("ZIP files (*.zip)",),
            )
        except Exception as exc:
            self._logger.error(f"export_product_package: save dialog failed: {exc}")
            return {"ok": False, "message": f"تعذر فتح نافذة الحفظ: {exc}"}

        if not result:
            return {"ok": False, "message": "تم الإلغاء."}
        path = result[0] if isinstance(result, (list, tuple)) else result

        try:
            with open(path, "wb") as f:
                f.write(content)
        except OSError as exc:
            self._logger.error(f"export_product_package: write failed: {exc}")
            return {"ok": False, "message": f"فشل حفظ الملف: {exc}"}

        self._logger.info(f"Exported product {product_id} package to {path}")
        return {"ok": True, "message": f"تم الحفظ في: {path}"}

    # ---- Receipt download (PDF / Excel) ----
    def save_receipt(self, transaction_id, fmt="pdf"):
        """Same fix as export_category_products above, applied to the
        receipt PDF/Excel downloads: pywebview's WebView has no real
        browser download manager behind it, so a Flask send_file()
        response - even with Content-Disposition: attachment set - tends
        to just try to render inline and show a blank/broken page instead
        of prompting to save. This calls straight into the service layer
        and hands the bytes to pywebview's native Save dialog instead, so
        there's no HTTP download involved and nothing for the WebView to
        get confused about.

        db_cursor() (app/db.py) reads through Flask's `g` / `current_app`,
        which only resolve inside an active app context - hence wrapping
        the service calls in `with self._server.app.test_request_context():`
        below. Calling into app/services directly from here without that
        wrapper raises "Working outside of application context". A plain
        app_context() alone isn't enough either: rendering the receipt
        template calls things like url_for() and the branding() helper,
        which need an active *request* context (not just an app context)
        or they raise "Working outside of request context". test_request_
        context() pushes both at once, so nothing here has to guess which
        one a given helper actually needs.
        """
        from app.services import sales as sales_service
        from app.services import receipts as receipt_service

        fmt = "excel" if fmt == "excel" else "pdf"

        try:
            with self._server.app.test_request_context():
                txn = sales_service.get_transaction(transaction_id)
                if not txn:
                    return {"ok": False, "message": "لم يتم العثور على عملية البيع."}
                if fmt == "excel":
                    content = receipt_service.receipt_excel_bytes(txn)
                    if hasattr(content, "getvalue"):
                        content = content.getvalue()
                    default_name = f"alqemma_receipt_{transaction_id}.xlsx"
                    file_types = ("Excel files (*.xlsx)",)
                else:
                    content = receipt_service.receipt_pdf_bytes(txn)
                    default_name = f"alqemma_receipt_{transaction_id}.pdf"
                    file_types = ("PDF files (*.pdf)",)
        except Exception as exc:
            self._logger.exception(f"save_receipt: failed to generate {fmt} for transaction {transaction_id}")
            return {"ok": False, "message": f"تعذر إنشاء الإيصال: {exc}"}

        try:
            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                directory="",
                save_filename=default_name,
                file_types=file_types,
            )
        except Exception as exc:
            self._logger.exception("save_receipt: save dialog failed")
            return {"ok": False, "message": f"تعذر فتح نافذة الحفظ: {exc}"}

        if not result:
            return {"ok": False, "message": "تم الإلغاء."}
        path = result[0] if isinstance(result, (list, tuple)) else result

        try:
            with open(path, "wb") as f:
                f.write(content)
        except OSError as exc:
            self._logger.exception("save_receipt: write failed")
            return {"ok": False, "message": f"فشل حفظ الملف: {exc}"}

        self._logger.info(f"Saved {fmt} receipt for transaction {transaction_id} to {path}")
        return {"ok": True, "message": f"تم الحفظ في: {path}", "path": path}

    # ---- Receipt print ("طباعة الإيصال") ----
    def print_receipt(self, transaction_id):
        """Replaces the old approach of navigating the pywebview window
        to a print page (receipt_print.html / sales.transaction_print) -
        this app is a single WebView window with no back button, so
        navigating it away to a receipt page trapped the user there with
        no way back to the rest of the app.

        Instead this reuses the exact same native Save-As flow as
        save_receipt(), forced to PDF, and then makes one best-effort,
        silent attempt to print that same file to the OS's default
        printer via _print_file_silently() - if there's no printer, or
        the print attempt fails, nothing is shown to the user for that
        part; the file the user just explicitly chose to save is the
        part that has to succeed and get reported, printing on top of
        it is opportunistic.
        """
        from app.services import sales as sales_service
        from app.services import receipts as receipt_service

        try:
            with self._server.app.test_request_context():
                txn = sales_service.get_transaction(transaction_id)
                if not txn:
                    return {"ok": False, "message": "لم يتم العثور على عملية البيع."}
                content = receipt_service.receipt_pdf_bytes(txn)
        except Exception as exc:
            self._logger.exception(f"print_receipt: failed to generate PDF for transaction {transaction_id}")
            return {"ok": False, "message": f"تعذر إنشاء الإيصال: {exc}"}

        try:
            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                directory="",
                save_filename=f"alqemma_receipt_{transaction_id}.pdf",
                file_types=("PDF files (*.pdf)",),
            )
        except Exception as exc:
            self._logger.exception("print_receipt: save dialog failed")
            return {"ok": False, "message": f"تعذر فتح نافذة الحفظ: {exc}"}

        if not result:
            return {"ok": False, "message": "تم الإلغاء."}
        path = result[0] if isinstance(result, (list, tuple)) else result

        try:
            with open(path, "wb") as f:
                f.write(content)
        except OSError as exc:
            self._logger.exception("print_receipt: write failed")
            return {"ok": False, "message": f"فشل حفظ الملف: {exc}"}

        self._logger.info(f"Saved receipt for transaction {transaction_id} to {path}, attempting print")

        # Silent, best-effort - deliberately not wrapped around a return,
        # so nothing here can turn a successful save into a reported
        # failure just because printing itself didn't work.
        try:
            _print_file_silently(path, self._logger)
        except Exception as exc:
            self._logger.exception("print_receipt: silent print attempt failed")

        return {"ok": True, "message": f"تم الحفظ في: {path}", "path": path}


# ============================================================
# Desktop / Startup shortcuts (Windows only, current user only)
# ============================================================
def _shortcut_target_and_args():
    """Returns (target_path, arguments) describing how to launch AlQemma
    from a .lnk file - the compiled exe itself when frozen, or the current
    Python interpreter + this script's path in development."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    return sys.executable, f'"{os.path.abspath(__file__)}"'


def _shortcut_icon_location(target_path):
    """Prefer the app's own icon file (same one used by alqemma.spec) when
    it's present on disk (always true in dev; only true if ever bundled as
    a loose file when frozen). Otherwise fall back to the icon embedded in
    the target executable itself."""
    icon_path = os.path.join(BASE_DIR, "app", "static", "app_icon.ico")
    if os.path.isfile(icon_path):
        return f"{icon_path},0"
    return f"{target_path},0"


def _write_shortcut(shell, lnk_path, target_path, arguments, working_dir, icon_location, log):
    """Creates lnk_path if missing, or updates it in place if any property
    is stale - never creates a second/duplicate shortcut, since CreateShortCut
    always resolves to the one file at lnk_path. Returns True only if the
    shortcut was actually created or changed, so callers can tell a real
    update apart from a no-op verification pass."""
    try:
        os.makedirs(os.path.dirname(lnk_path), exist_ok=True)
        existed = os.path.isfile(lnk_path)
        shortcut = shell.CreateShortCut(lnk_path)

        needs_save = (
            not existed
            or (shortcut.TargetPath or "").lower() != target_path.lower()
            or (shortcut.Arguments or "") != arguments
            or (shortcut.WorkingDirectory or "").lower() != working_dir.lower()
            or (shortcut.Description or "") != SHORTCUT_DESCRIPTION
        )

        if needs_save:
            shortcut.TargetPath = target_path
            shortcut.Arguments = arguments
            shortcut.WorkingDirectory = working_dir
            shortcut.Description = SHORTCUT_DESCRIPTION
            shortcut.IconLocation = icon_location
            shortcut.WindowStyle = 1
            shortcut.save()
            log(f"Shortcut {'created' if not existed else 'updated'}: {lnk_path}")
            return True
        return False
    except Exception as exc:
        log(f"Failed to write shortcut '{lnk_path}': {exc}", level="error")
        return False


def _write_shortcut_powershell(lnk_path, target_path, arguments, working_dir, icon_location, log):
    """Fallback shortcut creation via PowerShell's own WScript.Shell COM
    object, used whenever the pywin32 path above is unavailable OR fails
    for any reason inside the frozen .exe.

    This exists because pywin32 + PyInstaller has a well-known class of
    bundling issue: the compiled pythoncom3xx.dll / pywintypes3xx.dll
    binaries pywin32 needs aren't always found correctly at runtime
    inside a onefile build, even with 'pythoncom'/'win32com.client'
    listed in hiddenimports (that only guarantees the .py wrapper gets
    imported - not that its native DLL dependency resolves on every
    machine). This is very plausibly why shortcut creation silently
    failed on a brand-new Windows machine - it may never even reach the
    point of creating the shortcut if pythoncom itself won't import or
    won't initialize there.

    PowerShell ships with every Windows install since Vista/7 and can
    drive the exact same underlying WScript.Shell COM object with zero
    extra Python dependencies - so this path works regardless of how
    pywin32 was (or wasn't) bundled."""
    def esc(s):
        # Escape characters that would break out of the double-quoted
        # PowerShell string literals below.
        return str(s).replace("`", "``").replace('"', '`"')

    ps_script = (
        '$ws = New-Object -ComObject WScript.Shell\n'
        f'$s = $ws.CreateShortcut("{esc(lnk_path)}")\n'
        f'$s.TargetPath = "{esc(target_path)}"\n'
        f'$s.Arguments = "{esc(arguments)}"\n'
        f'$s.WorkingDirectory = "{esc(working_dir)}"\n'
        f'$s.Description = "{esc(SHORTCUT_DESCRIPTION)}"\n'
        f'$s.IconLocation = "{esc(icon_location)}"\n'
        '$s.WindowStyle = 1\n'
        '$s.Save()\n'
    )
    try:
        os.makedirs(os.path.dirname(lnk_path), exist_ok=True)
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            reason = (result.stderr or result.stdout or "non-zero exit").strip()
            log(f"PowerShell shortcut creation failed for {lnk_path}: {reason}", level="error")
            return False
        log(f"Shortcut created/updated via PowerShell fallback: {lnk_path}")
        return True
    except Exception as exc:
        log(f"PowerShell shortcut fallback failed for {lnk_path}: {exc}", level="error")
        return False


def ensure_application_shortcuts(logger=None):
    """Ensures a 'AlQemma Store' shortcut exists on the current user's
    Desktop and in their Startup folder, pointing at the real AlQemma
    executable (or, in development, at the interpreter + run.py).

    - Windows only; a silent no-op on any other platform.
    - Current user only - no admin rights needed (Desktop/Startup paths
      come from the user's own environment, not per-machine locations).
    - Idempotent: safe to call on every launch. Existing shortcuts are
      left untouched unless something about them is stale, and are never
      duplicated.
    - Never raises - any failure here is logged and swallowed so it can
      never prevent the app from starting.
    - Tries pywin32 (COM) first; if that's unavailable or fails for any
      reason (including a fresh machine where the bundled pywin32 DLLs
      don't resolve correctly), falls back to PowerShell, which needs no
      bundled Python dependency at all. See _write_shortcut_powershell().

    Returns True if a shortcut was actually created or updated this call,
    False if both were already correct (or on any failure/non-Windows
    platform) - lets callers fire a "shortcut configured" notification
    only on a real change, not on every routine startup check.
    """
    def log(message, level="info"):
        if logger is None:
            print(message)
        elif level == "error":
            logger.error(message)
        else:
            logger.info(message)

    if sys.platform != "win32":
        return False

    if not os.environ.get("USERPROFILE") or not os.environ.get("APPDATA"):
        log("USERPROFILE or APPDATA environment variable is missing - cannot determine shortcut folders.", level="error")
        return False

    target_path, arguments = _shortcut_target_and_args()
    working_dir = BASE_DIR
    icon_location = _shortcut_icon_location(target_path)
    desktop_dir = os.path.join(os.environ["USERPROFILE"], "Desktop")
    startup_dir = os.path.join(
        os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
    )

    changed = False
    pywin32_succeeded = False

    try:
        import pythoncom
        import win32com.client

        com_initialized_here = False
        try:
            pythoncom.CoInitialize()
            com_initialized_here = True
        except Exception as exc:
            # This previously sat OUTSIDE any try/except and could raise
            # uncaught (e.g. RPC_E_CHANGEDMODE if pywebview's own WebView2
            # backend already initialized COM on this thread in a
            # different apartment mode) - violating this function's own
            # "never raises" contract and silently skipping shortcut
            # creation with no useful log line. COM being already
            # initialized on this thread is not fatal to Dispatch()
            # below, so this just logs and continues instead of bailing.
            log(f"pythoncom.CoInitialize() reported: {exc} - continuing without a fresh COM init.")

        try:
            shell = win32com.client.Dispatch("WScript.Shell")
            for folder in (desktop_dir, startup_dir):
                lnk_path = os.path.join(folder, SHORTCUT_NAME)
                if _write_shortcut(shell, lnk_path, target_path, arguments, working_dir, icon_location, log):
                    changed = True
            pywin32_succeeded = True
        finally:
            if com_initialized_here:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
    except Exception as exc:
        log(f"pywin32 shortcut creation unavailable/failed ({exc}) - falling back to PowerShell.", level="error")

    if not pywin32_succeeded:
        for folder in (desktop_dir, startup_dir):
            lnk_path = os.path.join(folder, SHORTCUT_NAME)
            already_existed = os.path.isfile(lnk_path)
            if _write_shortcut_powershell(lnk_path, target_path, arguments, working_dir, icon_location, log):
                # The PowerShell path always (over)writes rather than
                # checking staleness first like _write_shortcut() does,
                # so only count it as a real "change" the first time the
                # file is created - otherwise every single launch would
                # report a change and re-fire the "shortcut configured"
                # notification.
                if not already_existed:
                    changed = True

    return changed


# ============================================================
# Splash-screen driving (talks to LOADING_HTML over evaluate_js)
# ============================================================
def _notify_shortcuts_updated(server, logger):
    """Fires the same notify_event() channel used for connectivity edges
    (in-app toast, local desktop notification, and web push to any
    subscribed device) after ensure_application_shortcuts() actually
    creates or updates a shortcut. Only called on a real change, not on
    every startup's routine verification. Never raises - a notification
    failure must not be able to affect startup."""
    if server.app is None:
        return
    try:
        from app.routes.notifications import notify_event
        with server.app.app_context():
            notify_event(
                "shortcut_updated",
                "تم تكوين اختصار البرنامج",
                "تم تكوين/تحديث اختصار AlQemma Store على سطح المكتب، وسيعمل تلقائيًا مع تشغيل نظام التشغيل.",
                url="/settings/",
            )
    except Exception as exc:
        logger.info(f"Could not send shortcut notification: {exc}")


def _report_step(window, logger, index, message=None):
    text = message or STARTUP_STEPS[index]
    logger.info(text)
    progress_pct = int(round((index + 1) / len(STARTUP_STEPS) * 100))
    try:
        window.evaluate_js(f"window.setStep && window.setStep({progress_pct}, {json.dumps(text)})")
    except Exception:
        pass  # splash DOM not ready yet, or window already navigated away - never fatal


def _report_failure(window, logger, message):
    logger.error(message)
    try:
        window.evaluate_js(f"window.setFailed && window.setFailed({json.dumps(message)})")
    except Exception:
        pass


def _startup_sequence(window, server, logger):
    """Runs in the background thread pywebview hands to webview.start()'s
    `func` argument - i.e. after the GUI event loop is already up, so
    evaluate_js() calls against the splash window are safe to make."""

    def log_callback(message, level="info"):
        if level == "error":
            logger.error(message)
        else:
            logger.info(message)

    try:
        window.events.loaded.wait(5)
    except Exception:
        pass

    _report_step(window, logger, 0)  # Initializing...
    time.sleep(0.1)

    _report_step(window, logger, 1)  # Starting local server...
    try:
        server.create_app()
        server.start(log_callback=log_callback)
    except Exception as exc:
        _report_failure(window, logger, f"Failed to start the server: {exc}")
        return

    if not wait_for_server(server.host, server.port, timeout=10):
        _report_failure(window, logger, "The server did not respond in time. Check launcher.log for details.")
        return

    _report_step(window, logger, 2)  # Setting up shortcuts...
    try:
        if ensure_application_shortcuts(logger):
            _notify_shortcuts_updated(server, logger)
    except Exception as exc:
        # Belt-and-braces: ensure_application_shortcuts() already never
        # raises, but startup must never be blocked by this feature no
        # matter what.
        logger.error(f"ensure_application_shortcuts raised unexpectedly: {exc}")

    _report_step(window, logger, 3)  # Done.
    time.sleep(0.25)  # let the bar visibly reach 100% before navigating away

    try:
        window.load_url(server.local_url)
    except Exception as exc:
        logger.error(f"Could not redirect the window to the app: {exc}")

    # Kicked off last, after the window has already redirected into the
    # real app - LAN detection is instant, but Tailscale is a subprocess
    # call, and even with its own internal timeout there's no reason to
    # make the user wait on it before they can start using the app.
    server.start_network_detection(logger)

    # Runs for the lifetime of the app - re-checks internet + Tailscale
    # every 60s and pushes the result into the navbar dot via evaluate_js.
    server.start_connectivity_monitor(logger, window)


# ============================================================
# Headless console fallback (no display / no pywebview available)
# ============================================================
def run_console_launcher():
    logger = configure_file_logging()

    def log(message, level="info"):
        print(message)
        if level == "error":
            logger.error(message)
        else:
            logger.info(message)

    server = ServerController()
    try:
        server.create_app()
    except Exception:
        import traceback
        traceback.print_exc()
        input("\nAl-Qemma failed to start. Press Enter to close...")
        sys.exit(1)

    server.start(log_callback=log)

    try:
        if ensure_application_shortcuts(logger):
            _notify_shortcuts_updated(server, logger)
    except Exception as exc:
        logger.error(f"ensure_application_shortcuts raised unexpectedly: {exc}")

    if not wait_for_server(server.host, server.port, timeout=10):
        log("The server did not respond in time.", level="error")

    threading.Thread(target=lambda: webbrowser.open(server.local_url), daemon=True).start()

    # No pywebview window in console mode, so this only logs status (see
    # the `window is not None` guard in _connectivity_loop) - still useful
    # for diagnosing connectivity from the console/launcher.log directly.
    server.start_connectivity_monitor(logger, window=None)

    print("================================")
    print(" Al-Qemma is running")
    print(f" Local: {server.local_url}")
    print("================================")

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.stop()
        sys.exit(0)


# ============================================================
# Entry point
# ============================================================
def _gui_available():
    if not HAS_GUI:
        print(f"[Al-Qemma] pywebview isn't installed or failed to import ({_GUI_IMPORT_ERROR}).")
        print("[Al-Qemma] Falling back to console mode - run: pip install pywebview")
        return False
    if sys.platform not in ("win32", "darwin") and not os.environ.get("DISPLAY"):
        print("[Al-Qemma] No display detected (DISPLAY is not set) - falling back to console mode.")
        return False
    return True


# ============================================================
# WebView2 Runtime check (Windows only)
# ============================================================
# pywebview's Windows backend tries WebView2 (Chromium-based - full
# modern CSS support: custom properties/var(--...), flexbox, grid,
# everything style.css uses) first, and SILENTLY falls back to the
# decades-old "winforms"/mshtml engine (Internet Explorer's Trident
# engine under the hood) if WebView2 isn't found on the machine. Trident
# does not support CSS custom properties AT ALL - since every color,
# spacing, and radius in this app's stylesheet is defined via var(--...),
# the fallback renders as what looks like completely unstyled HTML, even
# though every file is present and correct. This is the actual cause of
# "brand fresh Windows machine looks buggy/unstyled, the window still
# opens" - not a bug in this app's own code or asset bundling.
#
# Most machines have WebView2 already (Windows 11 ships it; Windows 10
# gets it via Windows Update), which is why a dev machine or an already-
# updated machine never shows this. A machine that's never run Windows
# Update, or a minimal/LTSC image, often doesn't.
WEBVIEW2_RUNTIME_CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_BOOTSTRAPPER_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
WEBVIEW2_DOWNLOAD_TIMEOUT = 20   # seconds
WEBVIEW2_INSTALL_TIMEOUT = 90    # seconds

# How long to keep polling for WebView2 to become ready after kicking off
# an install attempt - covers both our own installer finishing its
# background registration and Windows Update finishing its own install
# of Edge/WebView2 concurrently on a freshly-imaged machine.
WEBVIEW2_WAIT_TIMEOUT = 90       # seconds, total
WEBVIEW2_POLL_INTERVAL = 3       # seconds between checks

# Standalone/offline WebView2 Runtime installer, bundled into the exe via
# alqemma.spec's datas entry (see BUILD_EXE.md for where to get this file).
# Shop PCs run with no/restricted internet (see app/__init__.py's
# register_no_cache_headers docstring - "runs entirely offline"), so the
# online bootstrapper below reliably fails there and every launch falls
# back to the unstyled MSHTML engine. This local installer needs no
# network at all. It's tried first; the online bootstrapper stays as a
# best-effort fallback for dev machines where the vendor file isn't present.
WEBVIEW2_OFFLINE_INSTALLER_PATH = os.path.join(
    BUNDLE_DIR, "vendor", "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
)


def is_webview2_runtime_installed():
    """Checks the registry for the WebView2 Evergreen Runtime - the same
    check Microsoft's own WebView2 SDK/bootstrapper uses (presence of a
    "pv" version value under the Runtime's known Client GUID, in either
    the per-machine or per-user EdgeUpdate Clients key)."""
    if sys.platform != "win32":
        return True
    try:
        import winreg
        for hive, subkey in (
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_RUNTIME_CLIENT_GUID}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_RUNTIME_CLIENT_GUID}"),
            (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_RUNTIME_CLIENT_GUID}"),
        ):
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    version, _ = winreg.QueryValueEx(key, "pv")
                    if version:
                        return True
            except OSError:
                continue
        return False
    except Exception:
        # Can't check for some unexpected reason - assume it's fine
        # rather than triggering an unnecessary download/install attempt
        # (worst case, this matches today's behavior).
        return True


def _download_file(url, dest_path, timeout):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AlQemma-Launcher"})
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest_path, "wb") as out:
            out.write(resp.read())
        return True
    except Exception:
        return False


def ensure_webview2_runtime(logger=None):
    """Best-effort, and never blocks startup forever: if the WebView2
    Runtime isn't detected yet, kicks off a silent install (from the
    bundled offline installer if present, else Microsoft's small online
    bootstrapper) and then PATIENTLY POLLS for up to
    WEBVIEW2_WAIT_TIMEOUT seconds rather than checking once.

    Why polling matters: on a freshly-imaged Windows 10 machine, Windows
    Update is very often mid-install of Edge/WebView2 in the background
    on first boot. A single check right after launch can see "not
    installed" even though it finishes moments later - and that's too
    late, because pywebview picks its rendering engine (EdgeChromium vs
    the old MSHTML fallback) once, at webview.start() time. Missing that
    window locks the app into MSHTML (no CSS support) for the whole
    session even though WebView2 becomes available 10 seconds later.
    Polling here, before any window is created, means we simply wait
    for whichever install (ours or Windows Update's own) finishes first.

    Must run BEFORE webview.create_window()/webview.start().

    If WebView2 never becomes available within the timeout (no internet,
    no bundled installer, a locked-down machine, etc.) this gives up,
    logs it, and returns False - the app still launches, just with the
    old mshtml/winforms fallback, same as if this function didn't exist.
    """
    def log(message, level="info"):
        if logger is None:
            print(message)
        elif level == "error":
            logger.error(message)
        else:
            logger.info(message)

    if sys.platform != "win32":
        return True
    if is_webview2_runtime_installed():
        return True

    log("WebView2 Runtime not detected yet - this is normal right after Windows Update on a fresh machine. Attempting install and waiting for it to become ready...")

    try:
        from plyer import notification
        notification.notify(
            title="AlQemma Store",
            message="جاري تجهيز التطبيق لأول مرة، من فضلك انتظر...",
            timeout=WEBVIEW2_WAIT_TIMEOUT,
        )
    except Exception:
        pass  # native toast is a nice-to-have, never worth blocking startup over

    if os.path.isfile(WEBVIEW2_OFFLINE_INSTALLER_PATH):
        try:
            subprocess.run(
                [WEBVIEW2_OFFLINE_INSTALLER_PATH, "/silent", "/install"],
                capture_output=True, timeout=WEBVIEW2_INSTALL_TIMEOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as exc:
            log(f"Bundled WebView2 installer did not complete cleanly: {exc} (continuing to wait - it may still finish, or Windows Update may install it independently).", level="error")
    else:
        # No bundled installer present (dev machine, or the vendor file
        # wasn't added before building - see BUILD_EXE.md). Best-effort
        # online fallback; fine if it fails, the poll loop below still
        # catches Windows Update finishing on its own.
        import tempfile
        fd, bootstrapper_path = tempfile.mkstemp(suffix=".exe", prefix="MicrosoftEdgeWebview2Setup_")
        os.close(fd)
        try:
            if _download_file(WEBVIEW2_BOOTSTRAPPER_URL, bootstrapper_path, timeout=WEBVIEW2_DOWNLOAD_TIMEOUT):
                subprocess.run(
                    [bootstrapper_path, "/silent", "/install"],
                    capture_output=True, timeout=WEBVIEW2_INSTALL_TIMEOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
        except Exception as exc:
            log(f"Online WebView2 bootstrapper did not complete cleanly: {exc} (continuing to wait).", level="error")
        finally:
            try:
                os.remove(bootstrapper_path)
            except OSError:
                pass

    waited = 0
    while waited < WEBVIEW2_WAIT_TIMEOUT:
        if is_webview2_runtime_installed():
            log(f"WebView2 Runtime became ready after {waited}s.")
            return True
        time.sleep(WEBVIEW2_POLL_INTERVAL)
        waited += WEBVIEW2_POLL_INTERVAL

    log(f"WebView2 Runtime still not detected after waiting {WEBVIEW2_WAIT_TIMEOUT}s - continuing anyway. The app will launch, but may render unstyled until WebView2 becomes available on a future launch.", level="error")
    return False


def main():
    if not _gui_available():
        run_console_launcher()
        return

    logger = configure_file_logging()
    # Must happen before webview.create_window()/webview.start() below -
    # see ensure_webview2_runtime()'s docstring for why this is what
    # actually fixes "looks like unstyled HTML" on a fresh machine.
    ensure_webview2_runtime(logger)

    server = ServerController()
    settings = SettingsManager()

    width, height = _clamp_window_size(
        settings.get("window_width", DEFAULT_WINDOW_WIDTH),
        settings.get("window_height", DEFAULT_WINDOW_HEIGHT),
    )
    resizable = bool(settings.get("resizable", True))

    backup_manager = BackupManager(logger=logger)

    # Shared between AppAPI.exit_application() and _handle_native_close()
    # below so a call to one can't run graceful_shutdown() a second time
    # via the other - see the docstring on graceful_shutdown() for why
    # that double-run is otherwise possible.
    shutdown_guard = {"done": False, "lock": threading.Lock()}

    # AppAPI needs a window reference for exit_application()'s
    # window.destroy() call, but the window doesn't exist until after
    # create_window() runs - build the api with window=None, then patch
    # the real reference in once we have it.
    api = AppAPI(
        window=None, server=server, settings=settings, logger=logger,
        shutdown_guard=shutdown_guard, backup_manager=backup_manager,
    )

    window = webview.create_window(
        WINDOW_TITLE,
        html=LOADING_HTML,
        width=width,
        height=height,
        min_size=WINDOW_MIN_SIZE,
        resizable=resizable,
        background_color="#1E1E1E",
        js_api=api,
    )
    api._window = window
    if server.app is not None:
        server.app.config["PYWEBVIEW_WINDOW"] = window

    # Clicking the native OS close button (the window's X) - or Windows
    # itself closing the app during a system shutdown - used to skip
    # Python entirely and just tear the window down: no backup, no clean
    # server stop. This routes it through the same graceful_shutdown the
    # in-app Exit button uses.
    #
    # No confirmation dialog: the close is allowed to start immediately,
    # same as any normal Windows app. If auto-backup is off, this is a
    # no-op passthrough - graceful_shutdown() finishes near-instantly and
    # the window closes right away, same as before.
    #
    # If auto-backup is on, the backup must not block the GUI thread -
    # doing so is exactly what makes Windows consider an app "not
    # responding" and offer to force-close it during a real shutdown,
    # which is the opposite of what's needed here. So instead: cancel
    # this close attempt (return False), show a small separate "Backing
    # up - do not close" popup so there's something visible even if the
    # main window is already mid-close, run the backup on a background
    # thread (keeping the process - and the GUI message loop - alive and
    # responsive), then destroy the window ourselves once the backup
    # actually finishes.
    #
    # window.destroy() re-fires this same `closing` event, so the guard
    # is checked first: once graceful_shutdown() has already run (either
    # from here or from the in-app Exit button), any further `closing`
    # event is that follow-up destroy() and is let through immediately -
    # no second popup, no second backup.
    _backup_popup = {"window": None}

    def _show_backup_popup():
        try:
            popup_html = """
            <html><head><meta charset="utf-8"><style>
              html, body { margin: 0; height: 100%; }
              body {
                display: flex; align-items: center; justify-content: center;
                background: #1E1E1E; color: #f2f2f2;
                font-family: "Segoe UI", Tahoma, Arial, sans-serif;
                font-size: 14px;
              }
            </style></head>
            <body>جارٍ إجراء نسخة احتياطية...</body></html>
            """
            _backup_popup["window"] = webview.create_window(
                "Backing up — do not close",
                html=popup_html,
                width=320,
                height=100,
                resizable=False,
                on_top=True,
            )
        except Exception as exc:
            logger.error(f"Could not show backup popup: {exc}")

    def _close_backup_popup():
        popup = _backup_popup.get("window")
        if popup is None:
            return
        try:
            popup.destroy()
        except Exception as exc:
            logger.error(f"Error closing backup popup: {exc}")

    def _handle_native_close():
        with shutdown_guard["lock"]:
            already_done = shutdown_guard["done"]

        if already_done:
            # Our own follow-up window.destroy() below, after the backup
            # finished - graceful_shutdown() already ran, let it close.
            return True

        if not settings.get("auto_backup_on_close", False):
            graceful_shutdown(server, settings, backup_manager, logger, shutdown_guard, reason="native window close (no backup)")
            return True

        _show_backup_popup()

        def _do_backup_then_close():
            graceful_shutdown(server, settings, backup_manager, logger, shutdown_guard, reason="native window close (after backup)")
            _close_backup_popup()
            try:
                window.destroy()
            except Exception as exc:
                logger.error(f"Error while closing window after backup: {exc}")

        threading.Thread(target=_do_backup_then_close, daemon=True).start()
        return False

    window.events.closing += _handle_native_close

    # pywebview owns the main OS thread from here on (webview.start()
    # blocks until every window is closed). Waitress runs in its own
    # daemon thread (started inside _startup_sequence -> server.start()),
    # and _startup_sequence itself runs in the background thread
    # webview.start() hands us via `func` - never on the main thread, so
    # it can safely block on wait_for_server() without freezing the
    # splash animation.
    webview.start(_startup_sequence, (window, server, logger), debug=False)


if __name__ == "__main__":
    main()