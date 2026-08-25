import io

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, send_file, jsonify, session

from app.routes.notifications import notify_event
from app.services import products as product_service
from app.services import sales as sales_service
from app.services import customers as customer_service
from app.services.customers import AmbiguousCustomerError
from app.services import receipts as receipt_service
from app.services import settings as settings_service
from app.services.sales import InsufficientStockError
from app.services import orders as order_service
from app.db import db_cursor

bp = Blueprint("sales", __name__, url_prefix="/sales")


@bp.route("/check-customer", methods=["POST"])
def check_customer():
    """Read-only AJAX precheck the sale form calls right before final
    submit. Never creates or modifies anything - just tells the frontend
    whether Rule 5's confirmation dialog needs to show first."""
    data = request.get_json(silent=True) or {}
    name = data.get("customer_name", "")
    phone = data.get("customer_phone", "")
    result = customer_service.check_customer_ambiguity(name, phone)
    return jsonify(result)


@bp.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        # ensure a service placeholder exists and use its id for service lines
        service_placeholder_id = product_service.get_service_placeholder_product()
        lines, error = _parse_lines(request.form, service_placeholder_id)
        if error:
            flash(error, "error")
            return redirect(url_for("sales.new"))

        payments = _parse_payments(request.form)
        customer_process = request.form.get("customer_process") == "on"
        payment_total = sum(p["amount"] for p in payments)
        sale_total = sum(line["quantity"] * line["selling_price"] for line in lines)

        # طلب توصيل (delivery order): creates the transaction + a linked
        # order exactly like a normal sale (order revenue = the line
        # total, same as sale_total below - never entered by hand). No
        # payment method is asked for the order revenue itself; it stays
        # financially pending until the order reaches وصل and payment is
        # confirmed on the order detail page.
        #
        # قيمة شحن الاوردر (shipping cost) is a SEPARATE concept - the
        # delivery company's own fee, charged immediately regardless of
        # whether the order is ever collected - and is deducted right
        # away from either الدرج or التحويلات الأونلاين per the cashier's
        # choice. See app/services/orders.py:create_order().
        is_delivery_order = request.form.get("is_delivery_order") == "on"
        delivery_provider = None
        shipping_cost = 0.0
        shipping_cost_source = "drawer"
        if is_delivery_order:
            delivery_provider = request.form.get("delivery_provider", "").strip()
            shipping_cost = request.form.get("shipping_cost", type=float) or 0.0
            shipping_cost_source = request.form.get("shipping_cost_source") or "drawer"
            if not delivery_provider:
                flash("اختر شركة الشحن.", "error")
                return redirect(url_for("sales.new"))
            if shipping_cost_source not in ("drawer", "online"):
                flash("اختر مصدر خصم قيمة الشحن.", "error")
                return redirect(url_for("sales.new"))
            payments = []
            payment_total = 0

        if not is_delivery_order and payment_total > sale_total + 0.009:
            flash("المبلغ المدفوع لا يمكن أن يتجاوز الإجمالي.", "error")
            return redirect(url_for("sales.new"))

        if not is_delivery_order and not customer_process and abs(payment_total - sale_total) > 0.009:
            flash("يجب أن يتطابق إجمالي المدفوع مع الإجمالي أو تفعيل خيار عملية العميل قبل إكمال البيع.", "error")
            return redirect(url_for("sales.new"))

        # One customer name/phone per transaction, entered once (not
        # per-line) and applied to every line in the sale - there's no
        # way for a single line to opt out and stay unattached.
        txn_customer_name = request.form.get("customer_name", "").strip()
        txn_customer_phone = request.form.get("customer_phone", "").strip()

        # Spec: طلب توصيل requires BOTH customer name and phone before
        # إتمام عملية الاوردر - unlike a normal sale, which only needs
        # one of them (see the remaining-debt check below).
        if is_delivery_order and not txn_customer_name:
            flash("اسم العميل مطلوب لإنشاء طلب توصيل.", "error")
            return redirect(url_for("sales.new"))
        if is_delivery_order and not txn_customer_phone:
            flash("رقم هاتف العميل مطلوب لإنشاء طلب توصيل.", "error")
            return redirect(url_for("sales.new"))

        # A sale left with unpaid remaining debt must be traceable to
        # someone - checking the actual remaining balance rather than just
        # the customer_process checkbox, so this can't be bypassed by a
        # stale/mismatched form state.
        remaining_debt = sale_total - payment_total
        if remaining_debt > 0.009 and not txn_customer_name and not txn_customer_phone:
            flash("لإتمام عملية بها مبلغ متبقٍ (دين)، يجب إدخال اسم العميل أو رقم هاتفه على الأقل.", "error")
            return redirect(url_for("sales.new"))

        txn_customer_id = None
        if txn_customer_name or txn_customer_phone:
            confirmed_customer_id = request.form.get("confirmed_customer_id", type=int)
            force_new_customer = request.form.get("force_new_customer") == "1"
            try:
                txn_customer_id = customer_service.resolve_customer(
                    txn_customer_name, txn_customer_phone,
                    confirmed_customer_id=confirmed_customer_id,
                    force_new=force_new_customer,
                )
            except AmbiguousCustomerError:
                # Shouldn't normally happen - the sale page checks this via
                # /sales/check-customer before ever submitting the form.
                # This is only a safety net (e.g. JS disabled).
                flash(
                    f'الاسم "{txn_customer_name}" موجود مسبقًا في قائمة العملاء. '
                    "يرجى تفعيل جافاسكريبت لاختيار العميل الصحيح أو إنشاء عميل جديد.",
                    "error",
                )
                return redirect(url_for("sales.new"))
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("sales.new"))

        # Applied uniformly to every line - one customer per transaction,
        # no per-line opt-out.
        for line in lines:
            line["customer_name"] = txn_customer_name or None
            line["customer_phone"] = txn_customer_phone or None
            line["customer_id"] = txn_customer_id

        receipt_requested = request.form.get("receipt_requested") == "on"
        receipt_format = request.form.get("receipt_format", "pdf")

        try:
            sale_date = request.form.get("sale_date") or None
            transaction_id = sales_service.create_transaction(lines, payments, receipt_requested, sale_date=sale_date)
        except (InsufficientStockError, ValueError) as e:
            flash(str(e), "error")
            return redirect(url_for("sales.new"))

        if is_delivery_order:
            try:
                order_id = order_service.create_order(
                    transaction_id, delivery_provider, shipping_cost, shipping_cost_source
                )
            except order_service.OrderError as e:
                flash(str(e), "error")
                return redirect(url_for("sales.transaction_detail", transaction_id=transaction_id))

            source_label = "الدرج" if shipping_cost_source == "drawer" else "التحويلات الأونلاين"
            title = "New Delivery Order"
            message = (
                f"تم إنشاء طلب توصيل جديد عبر {delivery_provider} — إجمالي الاوردر {sale_total:.2f} ج.م"
                + (f" — تكلفة شحن {shipping_cost:.2f} ج.م تم خصمها من {source_label}." if shipping_cost > 0 else ".")
            )
            notify_event("order_created", title, message, url=url_for("orders.detail", order_id=order_id))

            flash("تم إنشاء طلب التوصيل بنجاح — مبلغ الطلب لن يُحتسب ضمن التقارير حتى يصل ويتم تأكيد الدفع.", "success")
            return redirect(url_for("orders.detail", order_id=order_id))

        # Notify about the sale and any low-stock products
        product_names = []
        for line in lines:
            product = product_service.get_product(line["product_id"])
            if product:
                product_names.append(product["name"])

        products_text = ", ".join(product_names) if product_names else "منتجات"
        customer_text = txn_customer_name or txn_customer_phone or ""
        customer_part = f" للعميل {customer_text}" if customer_text else ""

        title = "New Sale!"
        message = f"تم بيع {products_text}{customer_part} بمبلغ {sale_total:.2f} ج.م."
        notify_event("sale", title, message, url=url_for("sales.transaction_detail", transaction_id=transaction_id))

        threshold = settings_service.low_stock_threshold()
        for line in lines:
            product = product_service.get_product(line["product_id"])
            if product and 0 < product["quantity"] <= threshold:
                low_title = f"⚠️ Low Stock: {product['name']} is down to {product['quantity']} units!"
                low_message = f"Inventory alert for {product['name']}: only {product['quantity']} units remain."
                notify_event("low_stock", low_title, low_message, url=url_for("products.detail", product_id=product["id"]))

        flash("Sale recorded.", "success")
        if receipt_requested:
            return redirect(url_for("sales.transaction_detail", transaction_id=transaction_id,
                                     download=receipt_format))
        return redirect(url_for("sales.transaction_detail", transaction_id=transaction_id))

    from datetime import date
    preselected_id = request.args.get("product_id", type=int)
    available_products = product_service.list_products()
    for prod in available_products:
        prod["image_url"] = url_for("media.product_image", filename=prod["thumbnail"]) if prod.get("thumbnail") else None
    if session.get("role") != "admin":
        available_products = [
            {k: v for k, v in prod.items() if k != "purchase_price"}
            for prod in available_products
        ]
    service_placeholder_id = product_service.get_service_placeholder_product()
    default_sale_date = date.today().isoformat()
    return render_template(
        "sales_new.html", products=available_products, preselected_id=preselected_id,
        service_placeholder_id=service_placeholder_id,
        payment_methods=sales_service.PAYMENT_METHODS, default_warranty_days=settings_service.warranty_days(),
        default_sale_date=default_sale_date,
        delivery_providers=order_service.list_providers(),
    )


@bp.route("/history")
def history():
    """Receipt-based Sales History: one row per transaction/receipt (not
    one row per sold item), same grouping style as the Customers page.
    See sales_service.list_transactions()."""
    date_from = request.args.get("from") or None
    date_to = request.args.get("to") or None
    search = request.args.get("q", "").strip()
    transactions = sales_service.list_transactions(date_from=date_from, date_to=date_to, query=search)
    return render_template("sales_history.html", transactions=transactions, date_from=date_from, date_to=date_to,
                            payment_labels=sales_service.PAYMENT_LABELS_AR, search=search)


@bp.route("/transaction/<int:transaction_id>")
def transaction_detail(transaction_id):
    txn = sales_service.get_transaction(transaction_id)
    if not txn:
        abort(404)
    return render_template("transaction_detail.html", txn=txn,
                            payment_labels=sales_service.PAYMENT_LABELS_AR,
                            auto_download=request.args.get("download"))


@bp.route("/transaction/<int:transaction_id>/receipt.pdf")
def receipt_pdf(transaction_id):
    txn = sales_service.get_transaction(transaction_id)
    if not txn:
        abort(404)
    pdf_bytes = receipt_service.receipt_pdf_bytes(txn)
    return send_file(io.BytesIO(pdf_bytes), as_attachment=True,
                      download_name=f"{txn['receipt_number']}.pdf", mimetype="application/pdf")


@bp.route("/transaction/<int:transaction_id>/receipt.xlsx")
def receipt_excel(transaction_id):
    txn = sales_service.get_transaction(transaction_id)
    if not txn:
        abort(404)
    try:
        buf = receipt_service.receipt_excel_bytes(txn)
    except Exception as e:
        flash(f"Couldn't generate the Excel receipt: {e}", "error")
        return redirect(url_for("sales.transaction_detail", transaction_id=transaction_id))
    return send_file(buf, as_attachment=True, download_name=f"{txn['receipt_number']}.xlsx",
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/transaction/<int:transaction_id>/delete", methods=["POST"])
def delete_transaction(transaction_id):
    """حذف on Sales History (replaces the old عرض button - the receipt
    number is already a link to transaction_detail, so عرض was
    redundant). Hard-deletes the whole receipt: every line, its
    warranties, stock is restored per line, and any customer left with
    no other active sales is cleaned up. See sales_service.delete_transaction."""
    try:
        # If this transaction is a delivery order, clean up the
        # shipping-cost purchase row FIRST (it has no FK back to the
        # transaction so it can't cascade-delete on its own). The order
        # row itself, its status history, and any sale_payments row
        # cascade-delete automatically via FK ON DELETE CASCADE once
        # sales_service.delete_transaction() below removes the
        # transaction - see app/services/orders.py:
        # cleanup_before_transaction_delete(). Net effect: deleting a
        # delivery order's sale leaves nothing behind in drawer/online
        # totals, exactly as if it never happened (spec §10/§11/§20).
        order_service.cleanup_before_transaction_delete(transaction_id)
        sales_service.delete_transaction(transaction_id)
        flash("تم حذف الإيصال بالكامل وإرجاع الكميات للمخزون.", "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("sales.history"))


@bp.route("/<int:sale_id>")
def detail(sale_id):
    sale = sales_service.get_sale_detail(sale_id)
    if not sale:
        abort(404)
    return render_template("sale_detail.html", sale=sale)


@bp.route("/<int:sale_id>/void", methods=["POST"])
def void(sale_id):
    """مرتجع - unchanged. Only ever affects quantity/stock: gives the
    stock back and flags the line voided, without deleting the row."""
    try:
        sales_service.void_sale(sale_id)
        flash("Sale removed. Stock and warranty were reversed.", "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("sales.history"))


@bp.route("/<int:sale_id>/delete", methods=["POST"])
def delete_line(sale_id):
    """حذف on Sale Detail - per product line, with a quantity picker.
    Same effect scope as مرتجع (quantity/stock only), but hard-deletes
    instead of voiding. A partial quantity just shrinks the line and
    returns that many units to stock; deleting the full quantity removes
    the line (and its warranty) entirely. See sales_service.delete_sale_line."""
    quantity = request.form.get("quantity", type=int)
    try:
        fully_deleted = sales_service.delete_sale_line(sale_id, quantity)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("sales.detail", sale_id=sale_id))

    flash("تم الحذف وإرجاع الكمية للمخزون.", "success")
    if fully_deleted:
        # The sale row (and possibly its whole transaction) no longer
        # exists, so there's nothing left at sales.detail(sale_id) to
        # redirect back to.
        return redirect(url_for("sales.history"))
    return redirect(url_for("sales.detail", sale_id=sale_id))


# ---------- helpers ----------

def _parse_lines(form, service_placeholder_id=None):
    """Product/service line items only. Customer name and phone are NO
    LONGER read here - they're a single transaction-level field parsed
    once in new(), not one input per line."""
    product_ids = form.getlist("product_id")
    service_descriptions = form.getlist("service_description")
    custom_names = form.getlist("custom_product_name")
    quantities = form.getlist("quantity")
    prices = form.getlist("selling_price")
    warranty_days_list = form.getlist("warranty_days")

    lines = []
    for i, pid in enumerate(product_ids):
        service_description = service_descriptions[i].strip() if i < len(service_descriptions) else ""
        if not pid and not service_description:
            continue
        try:
            quantity = int(quantities[i]) if i < len(quantities) and quantities[i] else 1
            selling_price = float(prices[i]) if i < len(prices) and prices[i] else 0
        except ValueError:
            return None, "Quantity and price must be numbers."

        warranty_override = None
        if i < len(warranty_days_list) and warranty_days_list[i].strip():
            try:
                warranty_override = int(warranty_days_list[i])
            except ValueError:
                pass

        pid_val = int(pid) if pid else (service_placeholder_id if service_description else None)
        custom_name = custom_names[i].strip() if i < len(custom_names) else ""
        lines.append({
            "product_id": pid_val,
            "service_description": service_description or None,
            "custom_product_name": (custom_name or None) if not service_description else None,
            "quantity": quantity,
            "selling_price": selling_price,
            "warranty_days": warranty_override,
        })

    if not lines:
        return None, "Add at least one product to the sale."
    return lines, None


def _parse_payments(form):
    methods = form.getlist("payment_method")
    amounts = form.getlist("payment_amount")
    payments = []
    for i, method in enumerate(methods):
        if not method:
            continue
        amount_str = amounts[i] if i < len(amounts) else ""
        try:
            amount = float(amount_str) if amount_str else 0
        except ValueError:
            amount = 0
        if amount > 0:
            payments.append({"method": method, "amount": amount})
    return payments
