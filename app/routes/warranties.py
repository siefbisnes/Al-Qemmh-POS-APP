from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.services import warranties as warranty_service

bp = Blueprint("warranties", __name__, url_prefix="/warranties")


@bp.route("/")
def index():
    search = request.args.get("q", "").strip() or None
    date_from = request.args.get("from") or None
    date_to = request.args.get("to") or None

    return render_template(
        "warranties.html",
        active=warranty_service.list_active(search=search, date_from=date_from, date_to=date_to),
        expired=warranty_service.list_expired(search=search, date_from=date_from, date_to=date_to),
        search=search or "",
        date_from=date_from,
        date_to=date_to,
    )


@bp.route("/<int:warranty_id>/delete", methods=["POST"])
def delete(warranty_id):
    """حذف - standalone. Removes only this warranty row; never touches
    سجل المبيعات, stock, or anything else (see warranty_service.delete_warranty)."""
    if not warranty_service.get(warranty_id):
        flash("الضمان غير موجود أو تم حذفه بالفعل.", "error")
        return redirect(url_for("warranties.index"))
    warranty_service.delete_warranty(warranty_id)
    flash("تم حذف الضمان.", "success")
    return redirect(url_for("warranties.index"))
