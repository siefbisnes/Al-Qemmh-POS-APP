import os
import hashlib
import json
import platform
import uuid
from flask import Flask, session
from config import Config, BUNDLE_DIR
from app import db as db_module


def create_app(config_class=Config):
    static_folder = os.path.join(BUNDLE_DIR, "app", "static")
    template_folder = os.path.join(BUNDLE_DIR, "app", "templates")

    app = Flask(__name__, static_folder=static_folder, template_folder=template_folder)
    app.config.from_object(config_class)

    app.logger.info(f"static_folder = {static_folder} (exists: {os.path.isdir(static_folder)})")
    app.logger.info(f"template_folder = {template_folder} (exists: {os.path.isdir(template_folder)})")
    style_css_path = os.path.join(static_folder, "css", "style.css")
    app.logger.info(f"style.css present: {os.path.isfile(style_css_path)} ({style_css_path})")

    db_module.init_app(app)
    db_module.init_db(app)
    register_blueprints(app)
    register_error_handlers(app)
    register_template_helpers(app)
    register_auth_guard(app)
    register_no_cache_headers(app)
    register_changelog_routes(app)
    return app


def register_blueprints(app):
    from app.routes.dashboard import bp as dashboard_bp
    from app.routes.products import bp as products_bp
    from app.routes.categories import bp as categories_bp
    from app.routes.compatibility import bp as compatibility_bp
    from app.routes.sales import bp as sales_bp
    from app.routes.warranties import bp as warranties_bp
    from app.routes.reports import bp as reports_bp
    from app.routes.customers import bp as customers_bp
    from app.routes.media import bp as media_bp
    from app.routes.branding import bp as branding_bp
    from app.routes.settings import bp as settings_bp
    from app.routes.expenses import bp as expenses_bp
    from app.routes.purchases import bp as purchases_bp
    from app.routes.auth import bp as auth_bp
    from app.routes.notifications import bp as notifications_bp
    from app.routes.orders import bp as orders_bp
    from app.routes.diagnostics import bp as diagnostics_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(categories_bp)
    app.register_blueprint(compatibility_bp)
    app.register_blueprint(sales_bp)
    app.register_blueprint(warranties_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(customers_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(branding_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(expenses_bp)
    app.register_blueprint(purchases_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(diagnostics_bp)


def register_error_handlers(app):
    from app.utils.errors import register_error_handlers as _register
    _register(app)


def register_template_helpers(app):
    from app.branding import get_branding

    @app.context_processor
    def inject_globals():
        from datetime import datetime
        return {
            "current_year": datetime.now().year,
            "store_name": "Al-Qemma",
            "current_username": session.get("username"),
            "is_admin": session.get("role") == "admin",
            "show_purchase_price": session.get("role") == "admin",
        }

    app.jinja_env.globals["branding"] = get_branding
    app.jinja_env.globals["config"] = app.config

    def connectivity_status():
        controller = app.config.get("SERVER_CONTROLLER")
        if controller is None:
            return {"status": "checking", "message": ""}
        try:
            return controller.get_connectivity_status()
        except Exception:
            return {"status": "checking", "message": ""}

    app.jinja_env.globals["connectivity_status"] = connectivity_status

    @app.template_filter("money")
    def money_filter(value):
        try:
            return f"{float(value):,.2f} EGP"
        except (TypeError, ValueError):
            return "-"


def register_no_cache_headers(app):
    @app.after_request
    def _disable_caching(response):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


# ============================================================
# "ما الجديد" popup - shown once per physical machine per changelog
# version (see CHANGELOG in config.py for the actual content).
#
# Detection is by machine fingerprint (hostname + network adapter MAC,
# hashed), never by IP - Tailscale/LAN addresses change constantly and
# don't identify the machine at all. The "seen" record is a single
# {fingerprint: last_seen_version} dict in the settings table, so even
# if the whole SQLite database is copied onto a different PC, that new
# PC still hasn't earned its own "seen" mark and the popup fires there
# correctly.
# ============================================================
_CHANGELOG_SETTINGS_KEY = "changelog_seen_by_machine"


def _machine_fingerprint():
    raw = f"{platform.node()}::{uuid.getnode()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _changelog_load_seen():
    from app.services import settings as settings_service
    raw = settings_service.get(_CHANGELOG_SETTINGS_KEY)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _changelog_pending_entries():
    from config import CHANGELOG
    if not CHANGELOG:
        return []

    fingerprint = _machine_fingerprint()
    seen = _changelog_load_seen()
    last_seen_version = seen.get(fingerprint)

    if last_seen_version is None:
        return list(CHANGELOG)  # this machine has never dismissed anything

    versions = [entry["version"] for entry in CHANGELOG]
    if last_seen_version not in versions:
        return list(CHANGELOG)  # unrecognized bookkeeping - safest default is to show everything

    idx = versions.index(last_seen_version)
    return CHANGELOG[idx + 1:]


def register_changelog_routes(app):
    from flask import jsonify
    from config import CHANGELOG, LICENSE_SHORT

    @app.route("/changelog/pending")
    def changelog_pending():
        entries = _changelog_pending_entries()
        return jsonify({
            "entries": entries,
            "license": LICENSE_SHORT if entries else None,
        })

    @app.route("/changelog/ack", methods=["POST"])
    def changelog_ack():
        from app.services import settings as settings_service
        if CHANGELOG:
            fingerprint = _machine_fingerprint()
            seen = _changelog_load_seen()
            seen[fingerprint] = CHANGELOG[-1]["version"]
            settings_service.set(_CHANGELOG_SETTINGS_KEY, json.dumps(seen))
        return jsonify({"ok": True})


def register_auth_guard(app):
    from flask import redirect, request, session, url_for
    import time

    @app.before_request
    def require_login():
        if request.endpoint is None:
            return
        if request.endpoint.startswith("static"):
            return
        if request.endpoint in {"auth.login", "auth.logout"}:
            return
        if request.endpoint.startswith("notifications."):
            return
        # Hardware diagnostics is meant to be usable from the lock/login
        # screen itself (spec: "must be triggered directly from the
        # desktop POS Log Screen / Lock Screen") - a cashier needs to be
        # able to test a dead mouse or keyboard BEFORE they can type a
        # password with it, so this can't require being logged in.
        if request.endpoint.startswith("diagnostics."):
            return

        if not session.get("logged_in"):
            session.clear()
            return redirect(url_for("auth.login"))

        idle_timeout = app.config.get("AUTH_IDLE_TIMEOUT", 2700)
        last_active = session.get("last_active")
        now = int(time.time())
        if last_active is None or now - int(last_active) > idle_timeout:
            session.clear()
            return redirect(url_for("auth.login"))

        session["last_active"] = now
