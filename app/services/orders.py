"""
Delivery orders (طلبات التوصيل).

Core rule (spec §21): creating an order is operationally complete, but
financially complete only once the order reaches وصل and a payment
method is recorded. Until then `orders.financially_completed_at` is
NULL, and every report / owner-dashboard query that sums sales revenue
must exclude the order's transaction while that's true (see
`pending_order_transaction_ids_clause()` below, reused from
app/services/owner_dashboard.py and app/services/reports.py).

An order is 1:1 with a transaction (app/services/sales.py already owns
transactions/sales/sale_payments) - this module never duplicates that
data, it only tracks delivery-specific state and, at the very end,
writes a normal sale_payments row via the existing payment system so
paid-money reporting doesn't need a second code path.
"""
from datetime import datetime

from app.db import db_cursor
from app.services import sales as sales_service

STATUS_FLOW = ["preparing", "shipping", "delivered"]
TERMINAL_STATUSES = {"not_delivered", "cancelled"}

STATUS_LABELS_AR = {
    "preparing": "تجهيز الاوردر",
    "shipping": "في الشحن",
    "delivered": "وصل",
    "not_delivered": "لم يصل",
    "cancelled": "الغاء الاوردر",
}


class OrderError(ValueError):
    pass


def _money(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# ---------- providers ----------

def list_providers():
    with db_cursor() as cur:
        return [dict(r) for r in cur.execute(
            "SELECT * FROM delivery_providers ORDER BY sort_order, name"
        ).fetchall()]


def add_provider(name):
    name = (name or "").strip()
    if not name:
        raise OrderError("اسم شركة الشحن مطلوب.")
    slug = "provider_" + "_".join(name.split()).lower()
    with db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT OR IGNORE INTO delivery_providers (slug, name, is_builtin, sort_order) "
            "VALUES (?, ?, 0, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM delivery_providers))",
            (slug, name),
        )
    return slug


# ---------- creation (called from the checkout route) ----------

def create_order(transaction_id, delivery_provider, shipping_cost, shipping_cost_source):
    """Called right after sales_service.create_transaction() when the
    cashier checked طلب توصيل.

    Two separate financial concepts, per spec §15 - never conflated:
      - order_amount (order REVENUE): the transaction's own line total,
        exactly like a normal sale - computed here, never entered by
        hand. Stays financially pending (no sale_payments row) until
        confirm_payment() at وصل.
      - shipping_cost (delivery EXPENSE): charged immediately, since
        the shipping cost is paid out at drop-off regardless of whether
        the order is ever collected. Booked as a normal `purchases` row
        on the chosen side (shipping_cost_source: 'drawer' | 'online'),
        going through the exact same Drawer/Online split Reports and
        the Owner Dashboard already use for every other purchase.

        A SECOND, offsetting purchases row is booked on the OTHER
        side with a NEGATIVE cost of the same amount - a purchases row
        with negative cost reduces that bucket's total purchases,
        which is mathematically identical to a credit/inflow wherever
        Reports sums "payments - purchases" per bucket. Net effect:
          - the chosen side goes down by shipping_cost (real expense)
          - the other side goes UP by shipping_cost (offsetting credit)
          - net profit is unaffected (the two purchases rows cancel out
            in any total-purchases-across-both-buckets sum)
        This models "the shipping fee is paid from one bucket but the
        same amount is transferred over from the other to cover it" -
        a pure drawer↔online rebalancing, not a real second expense.
    """
    delivery_provider = (delivery_provider or "").strip()
    if not delivery_provider:
        raise OrderError("اختر شركة الشحن.")
    try:
        shipping_cost = float(shipping_cost or 0)
    except (TypeError, ValueError):
        raise OrderError("قيمة شحن الاوردر غير صحيحة.")
    if shipping_cost_source not in ("drawer", "online"):
        raise OrderError("اختر مصدر خصم قيمة الشحن.")

    with db_cursor(commit=True) as cur:
        total_row = cur.execute(
            "SELECT COALESCE(SUM(quantity * selling_price), 0) AS total FROM sales "
            "WHERE transaction_id = ? AND is_voided = 0",
            (transaction_id,),
        ).fetchone()
        order_amount = float(total_row["total"] or 0)

        shipping_purchase_id = None
        shipping_offset_purchase_id = None
        if shipping_cost > 0:
            # payment_method here follows the existing purchases/expenses
            # convention (cash | online), same binary Reports already
            # splits Drawer vs Online by - NOT the sale_payments 3-way
            # (cash/vodafone_cash/instapay), which is a different table
            # for a different kind of money movement (money received,
            # not money paid out).
            purchase_method = "cash" if shipping_cost_source == "drawer" else "online"
            offset_method = "online" if shipping_cost_source == "drawer" else "cash"

            cur.execute(
                "INSERT INTO purchases (name, cost, payment_method) VALUES (?, ?, ?)",
                (f"شحن اوردر — {delivery_provider}", shipping_cost, purchase_method),
            )
            shipping_purchase_id = cur.lastrowid

            cur.execute(
                "INSERT INTO purchases (name, cost, payment_method) VALUES (?, ?, ?)",
                (f"تحويل مقابل شحن اوردر — {delivery_provider}", -shipping_cost, offset_method),
            )
            shipping_offset_purchase_id = cur.lastrowid

        cur.execute(
            """INSERT INTO orders
               (transaction_id, delivery_provider, status, order_amount,
                shipping_cost, shipping_cost_source, shipping_purchase_id,
                shipping_offset_purchase_id)
               VALUES (?, ?, 'preparing', ?, ?, ?, ?, ?)""",
            (transaction_id, delivery_provider, order_amount,
             shipping_cost, shipping_cost_source, shipping_purchase_id,
             shipping_offset_purchase_id),
        )
        order_id = cur.lastrowid
        cur.execute(
            "INSERT INTO order_status_history (order_id, from_status, to_status) VALUES (?, NULL, 'preparing')",
            (order_id,),
        )
    return order_id


# ---------- reads ----------

def _attach_transaction(order):
    txn = sales_service.get_transaction(order["transaction_id"])
    order["transaction"] = txn
    order["customer_name"] = txn["customer_name"] if txn else None
    order["customer_phone"] = txn["customer_phone"] if txn else None
    order["receipt_number"] = txn["receipt_number"] if txn else None
    return order


def set_tracking_number(order_id, tracking_number):
    """رقم البوصلة (tracking/waybill number) - optional, can be added or
    changed at any time regardless of order status, and can be left
    blank. Purely informational (not used in any financial logic), so
    no OrderError validation beyond trimming/normalizing blank input."""
    tracking_number = (tracking_number or "").strip() or None
    with db_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE orders SET tracking_number = ?, updated_at = ? WHERE id = ?",
            (tracking_number, datetime.utcnow().isoformat(timespec="seconds"), order_id),
        )


def get_order(order_id):
    with db_cursor() as cur:
        row = cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not row:
        return None
    return _attach_transaction(dict(row))


def get_order_by_transaction(transaction_id):
    with db_cursor() as cur:
        row = cur.execute("SELECT * FROM orders WHERE transaction_id = ?", (transaction_id,)).fetchone()
    return dict(row) if row else None


def list_orders(search=None):
    sql = """
        SELECT o.*,
               t.created_at AS transaction_created_at,
               t.receipt_number AS transaction_receipt_number,
               MAX(s.customer_name) AS customer_name,
               MAX(s.customer_phone) AS customer_phone,
               SUM(s.quantity * s.selling_price) AS transaction_total
        FROM orders o
        JOIN transactions t ON t.id = o.transaction_id
        JOIN sales s ON s.transaction_id = t.id AND s.is_voided = 0
        GROUP BY o.id
        ORDER BY o.created_at DESC, o.id DESC
    """
    with db_cursor() as cur:
        rows = [dict(r) for r in cur.execute(sql).fetchall()]

    for row in rows:
        row["receipt_number"] = sales_service.receipt_number(
            row["transaction_id"], row["transaction_created_at"], stored=row.get("transaction_receipt_number")
        )
        row["status_label"] = STATUS_LABELS_AR.get(row["status"], row["status"])

    if search:
        q = search.strip().lower()
        if q:
            rows = [
                r for r in rows
                if q in (r["customer_name"] or "").lower()
                or q in (r["customer_phone"] or "").lower()
                or q in r["receipt_number"].lower()
                or q in (r["delivery_provider"] or "").lower()
            ]
    return rows


def pending_order_transaction_ids():
    """Transaction ids whose money must NOT be counted yet anywhere in
    Reports / Owner Dashboard: any order that hasn't been financially
    completed (regardless of delivery status - preparing, shipping,
    delivered-but-payment-not-confirmed-yet, or even not_delivered/
    cancelled, since those never receive money either)."""
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT transaction_id FROM orders WHERE financially_completed_at IS NULL"
        ).fetchall()
    return [r["transaction_id"] for r in rows]


def pending_order_sql_exclusion(sales_alias="s"):
    """SQL fragment to AND onto any query that sums `sales`/`sale_payments`
    for revenue, so pending delivery-order money is excluded until وصل +
    payment confirmation. Usage:
        query + " AND " + pending_order_sql_exclusion("s")
    """
    return (
        f"NOT EXISTS (SELECT 1 FROM orders o WHERE o.transaction_id = {sales_alias}.transaction_id "
        f"AND o.financially_completed_at IS NULL)"
    )


# ---------- status transitions ----------

def advance_status(order_id, new_status):
    """Enforces the linear progression تجهيز → شحن → وصل - no skipping.
    وصل itself doesn't financially complete the order; that happens
    separately in confirm_payment() once the transfer method is
    recorded (spec §11)."""
    order = get_order(order_id)
    if not order:
        raise OrderError("الاوردر غير موجود.")
    current = order["status"]
    if current in TERMINAL_STATUSES:
        raise OrderError("هذا الاوردر منتهي بالفعل ولا يمكن تغيير حالته.")
    if new_status not in STATUS_FLOW:
        raise OrderError("حالة غير صحيحة.")

    current_index = STATUS_FLOW.index(current)
    new_index = STATUS_FLOW.index(new_status)
    if new_index != current_index + 1:
        raise OrderError("لا يمكن تخطي مراحل الشحن.")

    _set_status(order_id, current, new_status)
    if new_status == "delivered":
        # Stamps the moment وصل is reached - the Quick View "days since
        # order creation" counter (§9) stops advancing at this point.
        with db_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE orders SET delivered_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(timespec="seconds"), order_id),
            )


def mark_not_delivered(order_id):
    order = get_order(order_id)
    if not order:
        raise OrderError("الاوردر غير موجود.")
    if order["status"] in TERMINAL_STATUSES:
        raise OrderError("هذا الاوردر منتهي بالفعل.")
    _set_status(order_id, order["status"], "not_delivered")


def cancel_order(order_id):
    order = get_order(order_id)
    if not order:
        raise OrderError("الاوردر غير موجود.")
    if order["status"] in TERMINAL_STATUSES:
        raise OrderError("هذا الاوردر منتهي بالفعل.")
    _set_status(order_id, order["status"], "cancelled")


def _set_status(order_id, from_status, to_status):
    with db_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
            (to_status, datetime.utcnow().isoformat(timespec="seconds"), order_id),
        )
        cur.execute(
            "INSERT INTO order_status_history (order_id, from_status, to_status) VALUES (?, ?, ?)",
            (order_id, from_status, to_status),
        )


# ---------- financial completion (وصل + payment confirmation) ----------

def confirm_payment(order_id, payment_method, transfer_image_path=None, payment_amount=None):
    """Only allowed once status == 'delivered'. Writes a normal
    sale_payments row for the order's transaction (reusing the existing
    payment system) and stamps financially_completed_at, which is what
    removes the transaction from pending_order_* exclusion everywhere in
    Reports / Owner Dashboard.

    payment_amount lets the cashier confirm for LESS than the order's
    snapshot order_amount (customer paid less than agreed at وصل). When
    omitted/None, behaves exactly as before - full order_amount is paid.
    A shortfall still stamps financially_completed_at (the order is
    considered "closed" either way per spec §21/§11), so its revenue and
    COGS are recognized in Reports/Owner Dashboard immediately, even
    though "المتبقي" on the transaction will show the unpaid remainder
    from here on - that remainder is a real, visible customer debt now,
    not something this function tracks separately.

    Shipping-cost source is fixed at order creation (§1 of the latest
    spec) - there is deliberately no manual drawer↔online toggle here
    anymore. Moving it earlier was flagged as a bug (only worked one
    direction) and, more importantly, the spec now explicitly says this
    should never be a manual action on this page at all."""
    order = get_order(order_id)
    if not order:
        raise OrderError("الاوردر غير موجود.")
    if order["status"] != "delivered":
        raise OrderError("يجب أن يصل الاوردر أولاً قبل تسجيل الدفع.")
    if payment_method not in dict(sales_service.PAYMENT_METHODS):
        raise OrderError("طريقة الدفع غير صحيحة.")

    order_amount = _money(order["order_amount"])
    if payment_amount is None:
        amount = order_amount
    else:
        amount = _money(payment_amount)
        if amount <= 0:
            raise OrderError("قيمة الدفع يجب أن تكون أكبر من صفر.")
        if amount > order_amount + 0.009:
            raise OrderError("لا يمكن أن يتجاوز المبلغ المدفوع إجمالي الاوردر.")

    now = datetime.utcnow().isoformat(timespec="seconds")
    with db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO sale_payments (transaction_id, method, amount, created_at) VALUES (?, ?, ?, ?)",
            (order["transaction_id"], payment_method, amount, now),
        )
        cur.execute(
            """UPDATE orders SET payment_method = ?, money_transferred = 1, transfer_image_path = ?,
                                  financially_completed_at = ?, updated_at = ? WHERE id = ?""",
            (payment_method, transfer_image_path, now, now, order_id),
        )
    return amount < order_amount - 0.009  # True if this was a shortfall confirmation


# ---------- Quick View: order age (§9) ----------

def order_age_display(order):
    """'الايام من تكوين هذه الاوردر' — elapsed time from order creation
    until now, but frozen at delivered_at once the order has reached
    وصل (it "stops counting after the delivery is reached").

    Computed entirely inside SQLite via julianday() rather than by
    parsing timestamps in Python and diffing datetime objects. This is
    deliberate: created_at is stamped by SQLite's own datetime('now')
    (UTC) at INSERT time, so asking the DB to compare it against its
    own datetime('now') again guarantees both sides of the calculation
    come from the exact same clock. Mixing in Python's datetime.now()
    (local time) for either side is exactly what caused the previous
    bug - a fixed ~3 hour offset (Egypt's UTC+2/+3) that made every
    order look permanently stuck at "3 hours" no matter how much real
    time had actually passed."""
    if not order.get("created_at"):
        return None

    with db_cursor() as cur:
        row = cur.execute(
            """SELECT (julianday(COALESCE(delivered_at, datetime('now'))) - julianday(created_at))
                      * 24 * 60 * 60 AS elapsed_seconds
               FROM orders WHERE id = ?""",
            (order["id"],),
        ).fetchone()

    if not row or row["elapsed_seconds"] is None:
        return None

    total_minutes = max(int(row["elapsed_seconds"] // 60), 0)
    days, rem_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem_minutes, 60)

    if days > 0:
        return f"{days} يوم" + (f" و{hours} ساعة" if hours else "")
    if hours > 0:
        return f"{hours} ساعة" + (f" و{minutes} دقيقة" if minutes else "")
    return f"{max(minutes, 0)} دقيقة"


# ---------- deletion cascade (§10/§11/§20) ----------
#
# orders / order_status_history already cascade-delete automatically via
# FK ON DELETE CASCADE the moment the last sale line on a transaction is
# removed and sales_service deletes the transactions row (see
# app/db.py: PRAGMA foreign_keys = ON is set per-connection, and
# sale_payments already cascades the same way for the same reason).
# The one thing that ISN'T reachable by a DB foreign key is the
# `purchases` row booked for the shipping cost - purchases is a general
# ledger table with no relationship back to orders. This must be
# cleaned up explicitly, and BEFORE the transaction row disappears
# (call from the sale-deletion route while order/transaction still
# exist), so a deleted delivery order truly leaves no trace in the
# drawer/online totals (§20).

def cleanup_before_transaction_delete(transaction_id):
    order = get_order_by_transaction(transaction_id)
    if not order:
        return
    with db_cursor(commit=True) as cur:
        for key in ("shipping_purchase_id", "shipping_offset_purchase_id"):
            if order.get(key):
                cur.execute("DELETE FROM purchases WHERE id = ?", (order[key],))
        # sale_payments and the orders/order_status_history rows themselves
        # cascade automatically via FK ON DELETE CASCADE once the caller
        # proceeds to delete the transaction/sale lines.


def apply_return_shipping_bearer(transaction_id, shipping_cost_bearer):
    """Used by the ارجاع (return) flow specifically - NOT by plain حذف.
    Decides what happens to the shipping-cost purchase row (and its
    offsetting drawer↔online transfer row - see create_order()) when an
    order is being reversed:

      shipping_cost_bearer == 'shop'     - تحمل تكلفة الشحن: the shop
        eats the shipping cost since it already paid the delivery
        company and can't get that back - BOTH rows are left exactly as
        they were created (the real expense + its net-profit-neutral
        transfer offset), an accurate record of what actually happened.

      shipping_cost_bearer == 'customer' - تحمل العميل تكلفة الشحن: the
        customer covers the shipping cost themselves (outside the
        system), so the shop takes no loss - BOTH rows are deleted,
        same as plain حذف, fully reversing the transfer too.

    Call this BEFORE sales_service.delete_transaction() - it needs the
    order/transaction to still exist to look up the purchase ids."""
    order = get_order_by_transaction(transaction_id)
    if not order or shipping_cost_bearer == "shop":
        return
    with db_cursor(commit=True) as cur:
        for key in ("shipping_purchase_id", "shipping_offset_purchase_id"):
            if order.get(key):
                cur.execute("DELETE FROM purchases WHERE id = ?", (order[key],))
