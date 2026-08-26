from flask import Blueprint, current_app, jsonify

bp = Blueprint("connectivity", __name__)


@bp.route("/api/connectivity-status")
def status():
    """Same status the desktop app already gets pushed into it via
    window.evaluate_js (see ServerController._connectivity_loop in
    run.py) - exposed over plain HTTP too, since evaluate_js only
    reaches the native pywebview window and does nothing for a normal
    browser tab open on the LAN/Tailscale URL (see base.html's
    connectivity dot polling fallback for the other half of this fix).

    Behaves like any other page here (subject to the normal login
    guard in app/__init__.py's register_auth_guard) rather than being
    specially exempted - the browser tab calling this already holds
    the same session cookie as the logged-in page it's running on, so
    there's no situation where this needs to work without a login.
    """
    controller = current_app.config.get("SERVER_CONTROLLER")
    if controller is None:
        return jsonify({"status": "checking", "message": ""})
    return jsonify(controller.get_connectivity_status())
