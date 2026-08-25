from flask import Blueprint, redirect, url_for, flash

from app.services import purchases as purchase_service

bp = Blueprint("purchases", __name__, url_prefix="/purchases")


@bp.route("/", methods=["GET", "POST"])
def index():
    return redirect(url_for("expenses.index"))


@bp.route("/<int:purchase_id>/delete", methods=["POST"])
def delete(purchase_id):
    purchase_service.delete_purchase(purchase_id)
    flash("Purchase removed.", "success")
    return redirect(url_for("purchases.index"))
