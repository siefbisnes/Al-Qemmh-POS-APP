from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, abort, Response, current_app, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash

from app.services import settings as settings_service
from app.services import backup as backup_service
from app.services import categories as category_service
from app.services import products as product_service

bp = Blueprint("settings", __name__, url_prefix="/settings")


@bp.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        warranty_days = request.form.get("default_warranty_days", type=int)
        low_stock = request.form.get("low_stock_threshold", type=int)
        if warranty_days is not None and warranty_days >= 0:
            settings_service.set("default_warranty_days", warranty_days)
        if low_stock is not None and low_stock >= 0:
            settings_service.set("low_stock_threshold", low_stock)
        flash("Settings saved.", "success")
        return redirect(url_for("settings.index"))

    return render_template("settings.html", current=settings_service.all_settings(), categories=category_service.list_categories())


@bp.route("/change-password", methods=["POST"])
def change_password():
    """Accepts JSON: {old_password, new_password, confirm_password}
    Verifies the old password (supports legacy plain config fallback),
    hashes the new password and stores it using settings_service.set(...).
    Returns JSON with success/message.

    BUG FIX: this previously always validated against and overwrote the
    regular "user" account's password ("auth_password_hash"), regardless
    of who was actually logged in - so an admin trying to change their
    own password always got "old password incorrect" (their real
    password was never checked against the right value), and even if it
    had somehow passed, it would have silently changed the "user"
    account's password instead of the admin's. This now keys off the
    logged-in session's role and uses a SEPARATE stored hash per account
    ("admin_password_hash" for admin, "auth_password_hash" for user, same
    as before for that account) - each account's password change is now
    fully independent of the other's.
    """
    data = request.get_json(silent=True)
    if not data:
        return {"success": False, "message": "Expected JSON body."}, 400

    old = data.get("old_password", "")
    new = data.get("new_password", "")
    confirm = data.get("confirm_password", "")

    if not new or new != confirm:
        return {"success": False, "message": "New passwords do not match."}, 400

    if session.get("role") == "admin":
        hash_key = "admin_password_hash"
        fallback_password = current_app.config.get("ADMIN_PASSWORD", "Ahmed145@")
    else:
        hash_key = "auth_password_hash"
        fallback_password = current_app.config.get("AUTH_PASSWORD", "password")

    stored_hash = settings_service.get(hash_key)
    if stored_hash:
        valid_old = check_password_hash(stored_hash, old)
    else:
        valid_old = old == fallback_password

    if not valid_old:
        return {"success": False, "message": "Old password is incorrect."}, 403

    hashed = generate_password_hash(new)
    settings_service.set(hash_key, hashed)
    return {"success": True, "message": "Password changed."}, 200


@bp.route("/backup/db")
def backup_db():
    buf, filename = backup_service.backup_db_bytes()
    return send_file(buf, as_attachment=True, download_name=filename,
                      mimetype="application/x-sqlite3")


@bp.route("/backup/excel")
def backup_excel():
    buf, filename = backup_service.export_excel_bytes()
    return send_file(buf, as_attachment=True, download_name=filename,
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/backup/csv")
def backup_csv():
    buf, filename = backup_service.export_csv_zip_bytes()
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/zip")


@bp.route("/restore-instance", methods=["POST"])
def restore_instance():
    """Browser counterpart to the desktop app's native folder-restore
    (AppAPI.restore_instance_from_folder in run.py). A browser can't hand
    Flask a filesystem path, so instead the JS uploads every file inside
    the picked "instance" folder (via <input webkitdirectory>) alongside
    its relative path, and this rebuilds instance/ from them - so
    photos, logs, and anything else in the folder come along, not just
    the database.
    """
    import os
    import shutil
    import tempfile

    files = request.files.getlist("instance_files")
    paths = request.form.getlist("instance_paths")
    if not files or len(files) != len(paths):
        return {"success": False, "message": "لم يتم اختيار أي ملفات."}, 400

    instance_dir = current_app.instance_path
    staging = tempfile.mkdtemp(prefix="instance_restore_")

    try:
        for f, relpath in zip(files, paths):
            relpath = (relpath or "").replace("\\", "/").strip()
            if not relpath:
                continue

            # Normalize the incoming path. Some browsers submit the picked
            # folder's own name as the first segment, while others submit a
            # root-relative file like "alqemma.db". Both should restore into
            # the target instance folder, so we preserve a root-level file and
            # drop only a leading folder name when the path is clearly a
            # folder-relative entry.
            parts = [p for p in relpath.split("/") if p not in ("", ".", "..")]
            if not parts:
                continue
            if len(parts) > 1:
                parts = parts[1:]
            dest = os.path.join(staging, *parts)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            f.save(dest)

        if not os.listdir(staging):
            shutil.rmtree(staging, ignore_errors=True)
            return {"success": False, "message": "المجلد المختار فارغ."}, 400

        if os.path.isdir(instance_dir):
            shutil.rmtree(instance_dir)
        shutil.move(staging, instance_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        current_app.logger.exception("restore_instance failed")
        return {"success": False, "message": "فشل استبدال البيانات."}, 500

    return {"success": True, "message": "تم استبدال البيانات بنجاح. الرجاء إعادة تشغيل التطبيق."}, 200


@bp.route("/export-category")
def export_category():
    category_id = request.args.get("category_id", type=int)
    if not category_id:
        abort(400)

    category = category_service.get_category(category_id)
    if not category:
        abort(404)

    import io

    products = product_service.list_products(category_id=category_id, include_inactive=True, include_sold=True)
    def format_price(value):
        try:
            price = float(value)
        except (TypeError, ValueError):
            return "0"
        return str(int(price)) if price.is_integer() else f"{price:.2f}"

    lines = [f"{product['name']} {format_price(product.get('selling_price'))}EGP" for product in products]
    text = "\n".join(lines)
    filename = f"category_{category['slug'] or category_id}_products.txt"

    buf = io.BytesIO(text.encode("utf-8"))
    buf.seek(0)
    return send_file(buf,
                     as_attachment=True,
                     download_name=filename,
                     mimetype="text/plain; charset=utf-8")


@bp.route("/export-category-data")
def export_category_data():
    """JSON counterpart to export_category() above, used by the Settings
    page's category-export button. The old route already set
    Content-Disposition correctly via send_file(as_attachment=True) - the
    real problem was that pywebview's embedded webview engine doesn't
    reliably trigger a native download when navigated to a URL via
    window.location.href, and just renders the response body as text
    instead. Returning plain JSON here sidesteps that entirely: the
    browser-side JS builds the .txt content and triggers the download
    itself via a Blob + <a download> link, which works the same inside
    pywebview and in a plain browser.

    Same query params and price formatting as export_category() so the
    two stay consistent if you ever need both.
    """
    category_id = request.args.get("category_id", type=int)
    if not category_id:
        return jsonify({"ok": False, "message": "الرجاء اختيار فئة أولاً."})

    category = category_service.get_category(category_id)
    if not category:
        return jsonify({"ok": False, "message": "الفئة غير موجودة."})

    products = product_service.list_products(category_id=category_id, include_inactive=True, include_sold=True)
    if not products:
        return jsonify({"ok": False, "message": "لا توجد منتجات في هذا القسم."})

    def format_price(value):
        try:
            price = float(value)
        except (TypeError, ValueError):
            return "0"
        return str(int(price)) if price.is_integer() else f"{price:.2f}"

    items = [{"name": p["name"], "price": format_price(p.get("selling_price"))} for p in products]
    return jsonify({"ok": True, "items": items})
