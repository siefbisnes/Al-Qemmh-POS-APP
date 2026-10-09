import logging
import os
import sqlite3

from flask import render_template, flash, redirect, url_for, request, session

from app.db import DatabaseBusyError
from app.services.sales import InsufficientStockError

logger = logging.getLogger("alqemma")


def register_error_handlers(app):
    log_path = os.path.join(os.path.dirname(app.config["DATABASE_PATH"]), "app.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    handler = logging.FileHandler(log_path)
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    if not logger.handlers:
        logger.addHandler(handler)
    logger.setLevel(logging.WARNING)

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        # After login, WebView2 sometimes replays POST onto the next page
        # (/about is GET-only) and shows Werkzeug's English 405 screen.
        # Send the user to a safe GET page instead of a dead end.
        logger.warning("Method not allowed: %s %s", request.method, request.path)
        if session.get("logged_in"):
            return redirect(url_for("branding.about"), code=303)
        return redirect(url_for("auth.login"), code=303)

    @app.errorhandler(InsufficientStockError)
    def insufficient_stock(e):
        flash(str(e), "error")
        return redirect(url_for("dashboard.index"))

    @app.errorhandler(sqlite3.IntegrityError)
    def integrity_error(e):
        logger.warning("Integrity error: %s", e)
        flash("That action would conflict with existing data (duplicate name, or a missing related "
              "record). Nothing was saved.", "error")
        return redirect(url_for("dashboard.index"))

    @app.errorhandler(DatabaseBusyError)
    def database_busy(e):
        logger.warning("Database busy: %s", e)
        flash(str(e), "error")
        return redirect(url_for("dashboard.index"))

    @app.errorhandler(500)
    def server_error(e):
        logger.exception("Unhandled server error")
        return render_template("errors/500.html"), 500
