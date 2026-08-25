import os
import sys

# When run normally, BASE_DIR is the project folder and everything (code,
# templates, database) lives under it as usual.
#
# When frozen into an .exe by PyInstaller, the bundled code/templates/
# schema.sql get extracted to a TEMPORARY folder that is deleted again
# when the program closes - so the database and product photos must NOT
# live there, or every restart would wipe the shop's data. BUNDLE_DIR is
# "wherever the bundled read-only files are right now" (fine for
# schema.sql, templates, static assets). BASE_DIR is "next to the actual
# .exe" (the only place data is safe to persist between runs).
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
else:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    BUNDLE_DIR = BASE_DIR

INSTANCE_DIR = os.path.join(BASE_DIR, "instance")

class Config:
    # --- Core ---
    SECRET_KEY = os.environ.get("ALQEMMA_SECRET_KEY", "change-this-for-anything-public-facing")
    DATABASE_PATH = os.path.join(INSTANCE_DIR, "alqemma.db")
    SCHEMA_PATH = os.path.join(BUNDLE_DIR, "schema.sql")

    # --- File storage ---
    PRODUCT_IMAGES_DIR = os.path.join(INSTANCE_DIR, "product_images")
    TMP_DIR = os.path.join(INSTANCE_DIR, "tmp")
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024  # 25 MB per request (multiple photos)
    ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

    # --- Business rules ---
    DEFAULT_WARRANTY_DAYS = 7
    LOW_STOCK_THRESHOLD = 3

    # --- Authentication ---
    AUTH_USERNAME = os.environ.get("ALQEMMA_AUTH_USERNAME", "user")
    AUTH_PASSWORD = os.environ.get("ALQEMMA_AUTH_PASSWORD", "password")
    AUTH_IDLE_TIMEOUT = 45 * 60  # seconds of inactivity before re-login is required
    ADMIN_USERNAME = os.environ.get("ALQEMMA_ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("ALQEMMA_ADMIN_PASSWORD", "Ahmed145@")[0:255]

    # --- Web Push / PWA ---
    VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "BOpg6LStE5fE76DIgmT1ZKzaw9trSmZBhLtaXfzfuU5yiJA2skIBCDCIQXneO0uCaySkijDGJRZZbagkGMyVFSM")
    VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "ClXEGMnuSOlC2_7V_6YCBB6eqVg2rHAOZHU7cGL4DGg")
    VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:alerts@alqemma.local")
    PUSH_SUBSCRIPTIONS_PATH = os.path.join(INSTANCE_DIR, "push_subscriptions.json")

    # --- Tailscale OAuth (used by run.py's Tailscale repair flow for
    # automated stale-duplicate-device cleanup via the Tailscale API) ---
    # Create an OAuth client in the Tailscale admin console
    # (https://login.tailscale.com/admin/settings/oauth) scoped to
    # "devices:core" only, then set these as real environment variables
    # (or your OS's secret store) - never commit real values here.
    #   TAILSCALE_OAUTH_CLIENT_ID     e.g. "kXXXXXXXXXXXX"        (client ID, not secret)
    #   TAILSCALE_OAUTH_CLIENT_SECRET e.g. "tskey-client-kXXXXXXXXXXXX-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
    #   TAILSCALE_TAILNET             e.g. "example.com" or "you@github"
    TAILSCALE_OAUTH_CLIENT_ID = os.environ.get("TAILSCALE_OAUTH_CLIENT_ID", "knHAGKegyY11CNTRL")
    TAILSCALE_OAUTH_CLIENT_SECRET = os.environ.get("TAILSCALE_OAUTH_CLIENT_SECRET", "tskey-client-knHAGKegyY11CNTRL-Hb6zi5xhi6bBSgF1U4Gf5bVVaFjC4izAf")
    TAILSCALE_TAILNET = os.environ.get("TAILSCALE_TAILNET", "TwkJN2Hsc521CNTRL")
