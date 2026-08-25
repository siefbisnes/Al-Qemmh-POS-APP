import os

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app, send_from_directory

from app.services import orders as order_service
from app.services import sales as sales_service

bp = Blueprint("orders", __name__, url_prefix="/orders")


@bp.route("/")
def index():
    search = request.args.get("q", "").strip() or None
    items = order_service.list_orders(search=search)
    return render_template("orders.html", orders=items, search=search or "")


@bp.route("/<int:order_id>/transfer-image")
def transfer_image(order_id):
    """Serves the transfer-proof image saved by confirm_payment().
    instance/order_transfer_images isn't under app/static, so there was
    previously no URL that could ever load it - the <img> tag in
    order_detail.html had nothing to point at. This is that URL."""
    order = order_service.get_order(order_id)
    if not order or not order.get("transfer_image_path"):
        abort(404)
    upload_dir = os.path.join(current_app.instance_path, "order_transfer_images")
    return send_from_directory(upload_dir, order["transfer_image_path"])


@bp.route("/<int:order_id>")
def detail(order_id):
    order = order_service.get_order(order_id)
    if not order:
        abort(404)
    return render_template(
        "order_detail.html",
        order=order,
        providers=order_service.list_providers(),
        payment_methods=sales_service.PAYMENT_METHODS,
        payment_labels=sales_service.PAYMENT_LABELS_AR,
        order_age=order_service.order_age_display(order),
    )


@bp.route("/<int:order_id>/advance", methods=["POST"])
def advance(order_id):
    new_status = request.form.get("status")
    try:
        order_service.advance_status(order_id, new_status)
        flash("تم تحديث حالة الاوردر.", "success")
    except order_service.OrderError as e:
        flash(str(e), "error")
    return redirect(url_for("orders.detail", order_id=order_id))


@bp.route("/<int:order_id>/not-delivered", methods=["POST"])
def not_delivered(order_id):
    try:
        order_service.mark_not_delivered(order_id)
        flash("تم تسجيل الاوردر كـ لم يصل.", "success")
    except order_service.OrderError as e:
        flash(str(e), "error")
    return redirect(url_for("orders.detail", order_id=order_id))


@bp.route("/<int:order_id>/cancel", methods=["POST"])
def cancel(order_id):
    try:
        order_service.cancel_order(order_id)
        flash("تم إلغاء الاوردر.", "success")
    except order_service.OrderError as e:
        flash(str(e), "error")
    return redirect(url_for("orders.detail", order_id=order_id))


@bp.route("/<int:order_id>/confirm-payment", methods=["POST"])
def confirm_payment(order_id):
    payment_method = request.form.get("payment_method")
    transfer_image_path = None
    file = request.files.get("transfer_image")
    if file and file.filename:
        upload_dir = os.path.join(current_app.instance_path, "order_transfer_images")
        os.makedirs(upload_dir, exist_ok=True)
        safe_name = f"order_{order_id}_{file.filename}".replace("/", "_").replace("\\", "_")
        file.save(os.path.join(upload_dir, safe_name))
        transfer_image_path = safe_name

    try:
        order_service.confirm_payment(order_id, payment_method, transfer_image_path)
        flash("تم تسجيل الدفع — تم اعتماد المبلغ ماليًا في التقارير.", "success")
    except order_service.OrderError as e:
        flash(str(e), "error")
    return redirect(url_for("orders.detail", order_id=order_id))


@bp.route("/<int:order_id>/delete", methods=["POST"])
def delete(order_id):
    """حذف — wipes the order's transaction entirely, as if it never
    happened: full stock restore, shipping-cost purchase removed, no
    special accounting choice. No confirmation popup - the simple
    browser confirm() on the button is enough, since there's no
    financial decision to make here (unlike ارجاع below)."""
    order = order_service.get_order(order_id)
    if not order:
        abort(404)
    try:
        order_service.cleanup_before_transaction_delete(order["transaction_id"])
        sales_service.delete_transaction(order["transaction_id"])
        flash("تم حذف الاوردر بالكامل وإرجاع الكميات للمخزون.", "success")
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("orders.detail", order_id=order_id))
    return redirect(url_for("orders.index"))


@bp.route("/<int:order_id>/return", methods=["POST"])
def return_order(order_id):
    """ارجاع — same underlying wipe as حذف, but reached only after the
    orders-page confirmation popup, which asks who bears the shipping
    cost the shop already paid out (spec: shown only from the Orders
    page; the plain إرجاع button on Sales History keeps working exactly
    as before, no popup, full wipe every time)."""
    order = order_service.get_order(order_id)
    if not order:
        abort(404)
    shipping_cost_bearer = request.form.get("shipping_cost_bearer", "shop")
    if shipping_cost_bearer not in ("shop", "customer"):
        shipping_cost_bearer = "shop"
    try:
        order_service.apply_return_shipping_bearer(order["transaction_id"], shipping_cost_bearer)
        sales_service.delete_transaction(order["transaction_id"])
        flash("تم إرجاع الاوردر بالكامل وإرجاع الكميات للمخزون.", "success")
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("orders.detail", order_id=order_id))
    return redirect(url_for("orders.index"))


@bp.route("/<int:order_id>/tracking-number", methods=["POST"])
def tracking_number(order_id):
    order = order_service.get_order(order_id)
    if not order:
        abort(404)
    order_service.set_tracking_number(order_id, request.form.get("tracking_number"))
    flash("تم حفظ رقم البوصلة.", "success")
    return redirect(url_for("orders.detail", order_id=order_id))


@bp.route("/providers/add", methods=["POST"])
def add_provider():
    try:
        order_service.add_provider(request.form.get("name"))
        flash("تم إضافة شركة الشحن.", "success")
    except order_service.OrderError as e:
        flash(str(e), "error")
    return redirect(request.referrer or url_for("orders.index"))
