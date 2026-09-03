from flask import Blueprint, render_template

bp = Blueprint("diagnostics", __name__, url_prefix="/diagnostics")


@bp.route("/")
def index():
    """Hardware diagnostic suite (keyboard/mouse/monitor/storage tests).
    Deliberately exempt from the login requirement (see
    app/__init__.py:register_auth_guard) so it's reachable straight
    from the lock/login screen - a cashier needs to be able to test a
    dead mouse or keyboard before they can even type a password with
    it. Everything on this page runs client-side in the browser; there
    is nothing here that reads or writes the shop's database, so no
    server-side state/permissions beyond that exemption are needed."""
    return render_template("diagnostics.html")


@bp.route("/color-test")
def color_test():
    """Standalone page (no base.html - no nav/sidebar/toasts/etc.) for
    the monitor dead-pixel color test. Loaded into its OWN separate
    pywebview window (see AppAPI.open_color_test_window in run.py) so
    it's a genuinely separate OS window/surface from the main app -
    not just a fullscreen overlay inside the same pywebview window.
    Same login exemption as / above, for the same reason."""
    return render_template("color_test.html")
