from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from werkzeug.security import check_password_hash
from app.services import settings as settings_service

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    session.clear()

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        expected_user = current_app.config.get("AUTH_USERNAME", "user")
        admin_user = current_app.config.get("ADMIN_USERNAME", "admin")
        admin_password = current_app.config.get("ADMIN_PASSWORD", "Ahmed145@")

        # BUG FIX: this admin branch previously only ever checked
        # `password == admin_password` (the config default) and had no
        # way to know about a changed password at all - settings.py's
        # change_password() now stores admin changes under
        # "admin_password_hash" (see that file's docstring), so this
        # must check that stored hash first, exactly mirroring the
        # regular-user branch just below it.
        if username == admin_user:
            stored_admin_hash = settings_service.get("admin_password_hash")
            if stored_admin_hash:
                admin_password_ok = check_password_hash(stored_admin_hash, password)
            else:
                admin_password_ok = password == admin_password
            if admin_password_ok:
                session["logged_in"] = True
                session["username"] = admin_user
                session["role"] = "admin"
                session["last_active"] = __import__("time").time()
                session["login_time"] = session["last_active"]
                session.permanent = False
                # Welcome page first (has clear "لوحة المالك" CTA). Owner dash
                # stays reachable from nav / about — avoids a blank-looking
                # analytics page if Chart.js / scroll-reveal JS fails.
                return redirect(url_for("branding.about"))

        if username == expected_user:
            stored_hash = settings_service.get("auth_password_hash")
            if stored_hash:
                password_ok = check_password_hash(stored_hash, password)
            else:
                password_ok = password == current_app.config.get("AUTH_PASSWORD", "password")
            if password_ok:
                session["logged_in"] = True
                session["username"] = expected_user
                session["role"] = "user"
                session["last_active"] = __import__("time").time()
                session["login_time"] = session["last_active"]
                session.permanent = False
                return redirect(url_for("branding.about"))

        flash("اسم المستخدم أو كلمة المرور غير صحيحة.", "error")

    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
