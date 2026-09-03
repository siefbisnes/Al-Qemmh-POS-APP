from flask import Blueprint, render_template

bp = Blueprint("diagnostics", __name__, url_prefix="/diagnostics")


@bp.route("/")
def index():
    """Hardware diagnostic suite (keyboard/mouse/audio/display/storage/
    system/USB tests). Deliberately exempt from the login requirement
    (see app/__init__.py:register_auth_guard) so it's reachable straight
    from the lock/login screen - a cashier needs to be able to test a
    dead mouse or keyboard before they can even type a password with
    it. Everything on this page runs client-side in the browser; there
    is nothing here that reads or writes the shop's database, so no
    server-side state/permissions beyond that exemption are needed."""
    return render_template("diagnostics.html")
