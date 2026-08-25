import os
from flask import Flask, session
from config import Config, BUNDLE_DIR
from app import db as db_module


def create_app(config_class=Config):
    # Explicitly pinned to BUNDLE_DIR (config.py's "wherever the bundled
    # read-only files actually are" - _MEIPASS when frozen, the project
    # folder in dev) rather than leaving Flask to guess static_folder/
    # template_folder as paths *relative to this module's own __file__*.
    #
    # That implicit relative resolution is exactly the kind of thing
    # that can differ between machines running the same frozen .exe:
    # PyInstaller compiles this package's .py files into its internal
    # archive (not extracted to real files on disk), so app/__init__.py's
    # own __file__/root_path inside a frozen build is not guaranteed to
    # land at the same real, listable directory that alqemma.spec's
    # `datas` entries physically extracted app/static and app/templates
    # into - it can happen to resolve correctly on one machine/PyInstaller
    # version and not another, which matches "works on the machine we
    # built and tested it on, looks unstyled with broken JS on a
    # different fresh machine" exactly: Flask silently falls back to
    # serving nothing for /static/... (or nothing at all) rather than
    # erroring loudly, so CSS and JS just don't load with no obvious
    # cause. Pinning these to BUNDLE_DIR removes that ambiguity entirely
    # - the same absolute path config.py already uses correctly for
    # SCHEMA_PATH.
    static_folder = os.path.join(BUNDLE_DIR, "app", "static")
    template_folder = os.path.join(BUNDLE_DIR, "app", "templates")

    app = Flask(__name__, static_folder=static_folder, template_folder=template_folder)
    app.config.from_object(config_class)

    # Diagnostic logging so a future "looks unstyled on this one machine"
    # report is immediately answerable from launcher.log instead of being
    # unreproducible - confirms exactly where Flask is looking for these
    # files and whether they're actually there on THIS machine.
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

    @app.template_filter("money")
    def money_filter(value):
        try:
            return f"{float(value):,.2f} EGP"
        except (TypeError, ValueError):
            return "-"


def register_no_cache_headers(app):
    """This app runs entirely offline on a handful of shop devices, so
    the tiny bandwidth cost of always refetching is nothing - freshness
    is what actually matters. Added specifically because pywebview's
    WebKitGTK engine on Linux has been caching CSS/JS/HTML more
    aggressively (and less predictably) than a normal browser tab on
    the same machine, causing edited files to keep showing old
    behavior inside the native window even after being replaced on
    disk and restarting the app. This forces every response - static
    files and rendered pages alike - to be revalidated every time,
    which removes that whole class of "I changed the file but nothing
    changed" bug rather than chasing down the specific cache folder to
    clear for each engine/OS/version combination."""
    @app.after_request
    def _disable_caching(response):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


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