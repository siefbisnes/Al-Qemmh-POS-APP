from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from app.db import db_cursor
from app.services import sales as sales_service
from app.services import customers as customer_service
from app.services import orders as order_service

bp = Blueprint("customers", __name__, url_prefix="/customers")


@bp.route("/search")
def search():
    """JSON autocomplete for the sale/order form's customer name+phone
    fields (spec §3-6). Queries the customers table directly (name,
    phone straight from schema.sql) rather than going through
    list_customers(), which returns a debt-report-shaped row for the
    customers list page and doesn't guarantee plain name/phone keys -
    that mismatch was the "بلا اسم" bug. Matches by name OR phone
    against the same query. Capped at 10 to keep the dropdown light."""
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify([])
    like = f"%{q}%"
    with db_cursor() as cur:
        rows = cur.execute(
            """SELECT id, name, phone FROM customers
               WHERE name LIKE ? OR phone LIKE ?
               ORDER BY updated_at DESC
               LIMIT 10""",
            (like, like),
        ).fetchall()
    return jsonify([
        {"id": r["id"], "name": r["name"] or "", "phone": r["phone"] or ""}
        for r in rows
    ])


@bp.route("/")
def index():
    filter_value = request.args.get("filter", "")
    search = request.args.get("q", "").strip()
    customers = customer_service.list_customers(
        debtors_only=filter_value == "debtors",
        query=search,
    )
    return render_template(
        "customers/index.html",
        customers=customers,
        filter_value=filter_value,
        search=search,
    )


@bp.route("/<int:customer_id>")
def detail(customer_id):
    search = request.args.get("q", "").strip()
    customer = customer_service.get_customer(customer_id, query=search)
    if not customer:
        abort(404)
    return render_template("customers/detail.html", customer=customer, search=search)


@bp.route("/<int:customer_id>/edit", methods=["POST"])
def edit(customer_id):
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    try:
        customer_service.update_customer(customer_id, name, phone)
        flash("تم حفظ بيانات العميل بنجاح.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("customers.detail", customer_id=customer_id))


@bp.route("/purchase/<int:transaction_id>")
def purchase_detail(transaction_id):
    # Delivery-order guard (spec §12/§13): an unfinished delivery order
    # must never be treated as an ordinary customer purchase - the
    # payment/debt controls on this page assume a normal sale, and
    # letting someone "just pay it off" here would bypass the
    # تجهيز→شحن→وصل workflow and its own payment confirmation entirely.
    # Once the order IS financially completed (financially_completed_at
    # set), it behaves like any other paid purchase again and falls
    # through to the normal page below.
    order = order_service.get_order_by_transaction(transaction_id)
    if order and not order.get("financially_completed_at"):
        flash("لتكملة تفاصيل هذه البيعه اذهب لصفحة الطلبات", "info")
        return redirect(url_for("orders.detail", order_id=order["id"]))

    purchase = customer_service.get_purchase(transaction_id)
    if not purchase:
        abort(404)
    return render_template(
        "customers/purchase_detail.html",
        purchase=purchase,
        payment_methods=sales_service.PAYMENT_METHODS,
    )


@bp.route("/purchase/<int:transaction_id>/complete", methods=["POST"])
def complete_purchase(transaction_id):
    # Same guard as purchase_detail() above - block the POST too, not
    # just the page render, since a stale/cached page or a direct form
    # submit could otherwise still slip a payment through outside the
    # order workflow.
    order = order_service.get_order_by_transaction(transaction_id)
    if order and not order.get("financially_completed_at"):
        flash("لتكملة تفاصيل هذه البيعه اذهب لصفحة الطلبات", "info")
        return redirect(url_for("orders.detail", order_id=order["id"]))

    amount = request.form.get("payment_amount", type=float)
    method = request.form.get("payment_method") or "cash"
    try:
        customer_service.add_payment(transaction_id, amount, method=method)
        flash("تم تحديث مديونية العميل بنجاح.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("customers.purchase_detail", transaction_id=transaction_id))
