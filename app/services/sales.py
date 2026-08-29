"""
A "Selling" session is a transaction (app/db transactions table) made up of
one or more sale lines (one row per product sold, in the sales table) and
one or more payments (sale_payments table, since a customer can split a
bill across cash / Vodafone Cash / Instapay). Everything in one transaction
shares a single customer name + phone, entered once for the whole
transaction (see the "+ Another Sale" flow) and applied to every line -
there's no way for one line to opt out and stay unattached to the
transaction's customer.

A standalone, single-line sale (the old behaviour) is just a transaction
with exactly one line - nothing special about it.

Customer resolution (matching an existing customer or creating a new one)
happens in app/routes/sales.py before create_transaction is called - this
module just persists whatever customer_id it's given per line. In practice
every line in a transaction carries the same customer_name/customer_phone/
customer_id, since the route applies the one transaction-level value to
all of them.

DELETE vs VOID
--------------
void_sale() (مرتجع) and delete_sale_line() (حذف) intentionally have the
exact same real-world effect - they only ever touch quantity/stock, never
warranty logic beyond what already existed, never payments. The only
difference is void_sale() keeps the row (flagged is_voided=1, so there's
still a record it happened) while delete_sale_line() permanently removes
it (or shrinks its quantity). Both share the same ghost-customer cleanup
via _cleanup_ghost_customer() below, so that rule can't drift between the
two paths.
"""
from datetime import datetime, timedelta
import secrets
import sqlite3

from app.db import db_cursor
from app.services import settings as settings_service
from app.services import product_audit

PAYMENT_METHODS = [
    ("cash", "نقدي (Cash)"),
    ("vodafone_cash", "Vodafone Cash"),
    ("instapay", "Instapay"),
]
PAYMENT_LABELS_AR = {"cash": "نقدي", "vodafone_cash": "Vodafone Cash", "instapay": "Instapay"}

# Excludes 0/O and 1/I/L - characters that look alike when read off a
# printed receipt or typed into search. 6 characters from this 31-symbol
# alphabet is ~887 million possible codes; collisions are handled (see
# _generate_receipt_number) rather than assumed impossible, but in
# practice this shop will never see one.
_RECEIPT_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_RECEIPT_CODE_LENGTH = 6


def _generate_receipt_number(year):
    """A fresh 'INV-<year>-<random code>' string - NOT unique-checked
    here, that happens at the INSERT site (see create_transaction),
    which retries with a new call to this function on the rare
    collision. Deliberately not derived from transactions.id: an
    id-derived number breaks the moment two databases are ever merged
    (id sequences can collide across separate databases), while an
    independently-random code cannot."""
    code = "".join(secrets.choice(_RECEIPT_CODE_ALPHABET) for _ in range(_RECEIPT_CODE_LENGTH))
    return f"INV-{year}-{code}"


class InsufficientStockError(Exception):
    pass


def receipt_number(transaction_id, created_at, stored=None):
    """Canonical receipt/invoice number.

    `stored` is transactions.receipt_number when the caller already has
    it - a real, randomly-generated, permanently stored code (see
    _generate_receipt_number), immune to id collisions if a database is
    ever merged with another. Always prefer passing it when available.

    Falls back to the OLD id-derived 'INV-<year>-<6-digit id>' format
    ONLY when `stored` is empty - i.e. a transaction created before the
    receipt_number column existed. That keeps every already-printed
    paper receipt's number exactly what it always was; nothing already
    issued is ever renumbered by this change."""
    if stored:
        return stored
    year = (created_at or "")[:4] or "----"
    return f"INV-{year}-{int(transaction_id):06d}"


def create_transaction(lines, payments, receipt_requested=False, sale_date=None):
    """
    lines: [{product_id, customer_name, customer_phone, customer_id,
             quantity, selling_price, warranty_days, custom_product_name}]

    custom_product_name (optional, product lines only): a sale-specific
    display name override, e.g. "Samsung Monitor - Customer Special"
    instead of the catalog name. Stored on the sale row only - never
    written back to products.name, so inventory/search/other sales are
    unaffected. Ignored for service lines (service_description already
    names those).
    payments: [{method, amount}]

    customer_id is optional per line (defaults to None) - the route sets
    it to the same value on every line for a given transaction.

    Atomic: if any line can't be fulfilled (not enough stock), the whole
    transaction is rolled back - it's all-or-nothing, never half a sale.
    Returns the new transaction_id.
    """
    if not lines:
        raise ValueError("A transaction needs at least one sale line.")

    if sale_date:
        # accept date-only strings (YYYY-MM-DD) or full ISO
        if len(sale_date) == 10:
            sale_date = sale_date + "T00:00:00"
    else:
        sale_date = datetime.now().isoformat(timespec="seconds")

    for line in lines:
        if line.get("service_description"):
            continue
        quantity = line.get("quantity")
        selling_price = line.get("selling_price")
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            raise ValueError("الكمية يجب أن تكون رقمًا صحيحًا.")
        if quantity <= 0:
            raise ValueError("الكمية يجب أن تكون أكبر من صفر.")
        try:
            selling_price = float(selling_price)
        except (TypeError, ValueError):
            raise ValueError("السعر يجب أن يكون رقمًا صحيحًا.")
        if selling_price < 0:
            raise ValueError("السعر لا يمكن أن يكون سالبًا.")

    with db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO transactions (created_at, receipt_requested) VALUES (?, ?)",
            (sale_date, 1 if receipt_requested else 0),
        )
        transaction_id = cur.lastrowid

        # Generate + store the real receipt number now, once, rather
        # than deriving it from transaction_id on every future read -
        # see _generate_receipt_number's docstring for why. A UNIQUE
        # constraint violation here means the random code collided with
        # an existing one (astronomically unlikely at this alphabet/
        # length) - just draw a new one and try again.
        year = (sale_date or "")[:4] or datetime.now().strftime("%Y")
        for _ in range(5):
            candidate = _generate_receipt_number(year)
            try:
                cur.execute("UPDATE transactions SET receipt_number = ? WHERE id = ?",
                            (candidate, transaction_id))
                break
            except sqlite3.IntegrityError:
                continue
        else:
            raise RuntimeError("تعذر إنشاء رقم فاتورة فريد - حاول مرة أخرى.")

        transaction_total = sum(
            float(l.get("selling_price") or 0) * int(l.get("quantity") or 0) for l in lines
        )

        for line in lines:
            product_id = line["product_id"]
            quantity = int(line["quantity"])
            # Service lines carry a service_description and should not affect inventory
            if not line.get("service_description"):
                row = cur.execute("SELECT quantity FROM products WHERE id = ?", (product_id,)).fetchone()
                if row is None:
                    raise ValueError(f"Product {product_id} not found")
                if row["quantity"] < quantity:
                    raise InsufficientStockError(
                        f"Only {row['quantity']} in stock for that product, cannot sell {quantity}."
                    )
                cur.execute("UPDATE products SET quantity = quantity - ? WHERE id = ?", (quantity, product_id))
                product_row = cur.execute("SELECT name FROM products WHERE id = ?", (product_id,)).fetchone()
                product_audit.log_event(
                    product_id, product_row["name"] if product_row else f"#{product_id}",
                    "quantity_sale",
                    quantity_before=row["quantity"], quantity_after=row["quantity"] - quantity,
                    new_value=str(quantity), reference=str(transaction_id), reference_type="transaction",
                    note=f"إجمالي الفاتورة: {transaction_total:.2f} ج.م", cur=cur,
                )
                product_audit.record_expected_returns_snapshot(cur=cur)

            warranty_days = line.get("warranty_days")
            # Resolve blank/None to the store's configured default *before*
            # storing it - previously the raw (possibly blank) value was
            # written to sales.warranty_days while the resolved default was
            # only used for the warranties table's expiration date below,
            # so a sale left blank stored NULL and showed "no warranty" on
            # the receipt even though a real default warranty applied.
            # Service lines don't get a warranty at all, so their raw value
            # (normally None) is left as-is.
            resolved_warranty_days = warranty_days
            if not line.get("service_description"):
                resolved_warranty_days = warranty_days if warranty_days not in (None, "") else settings_service.warranty_days()

            # Sale-specific display name override for a real product line
            # (e.g. "Samsung Monitor - Customer Special" instead of the
            # catalog name). Deliberately independent of service_description
            # (which flags service lines and gates inventory/warranty logic
            # above) - this is purely cosmetic and never applies to service
            # lines, which already have their own name via service_description.
            custom_product_name = None
            if not line.get("service_description"):
                raw_custom_name = (line.get("custom_product_name") or "").strip()
                if raw_custom_name:
                    custom_product_name = raw_custom_name

            cur.execute(
                """INSERT INTO sales
                   (product_id, sale_date, selling_price, quantity, customer_name, customer_phone,
                    customer_id, transaction_id, warranty_days, service_description, custom_product_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (product_id, sale_date, line["selling_price"], quantity,
             line.get("customer_name") or None, line.get("customer_phone") or None,
             line.get("customer_id") or None,
             transaction_id, resolved_warranty_days, line.get("service_description"),
             custom_product_name),
            )
            sale_id = cur.lastrowid

            # Only create warranties for tangible products (not services)
            if not line.get("service_description"):
                expiration = datetime.fromisoformat(sale_date) + timedelta(days=int(resolved_warranty_days))
                cur.execute(
                    "INSERT INTO warranties (sale_id, start_date, expiration_date) VALUES (?, ?, ?)",
                    (sale_id, sale_date, expiration.isoformat(timespec="seconds")),
                )
        for p in payments:
            try:
                amount = float(p.get("amount", 0))
            except (TypeError, ValueError):
                amount = 0
            if amount <= 0:
                continue
            cur.execute(
                "INSERT INTO sale_payments (transaction_id, method, amount, created_at) VALUES (?, ?, ?, ?)",
                (transaction_id, p.get("method"), amount, sale_date),
            )

    return transaction_id


def _cleanup_ghost_customer(cur, customer_id):
    """Shared by void_sale() and delete_sale_line(): if a customer has no
    remaining active (non-voided, non-deleted) sales anywhere, their
    customer row is removed too. Otherwise they'd be an invisible "ghost" -
    gone from the Customers tab (which only lists customers with active
    history) but still sitting in the customers table, so the very next
    sale using their old name/phone would silently match and offer to
    attach to them again. Deleting frees that name/phone for a genuinely
    new customer."""
    remaining_active = cur.execute(
        "SELECT COUNT(*) FROM sales WHERE customer_id = ? AND is_voided = 0",
        (customer_id,),
    ).fetchone()[0]
    if remaining_active == 0:
        cur.execute("UPDATE sales SET customer_id = NULL WHERE customer_id = ?", (customer_id,))
        cur.execute("DELETE FROM customers WHERE id = ?", (customer_id,))


def void_sale(sale_id):
    """Reverses a sale line: gives the stock back, deletes its warranty,
    and marks it voided so reports/profit stop counting it - without
    deleting the sale row itself, so there's still a record that it
    happened and was reversed (and why, if a reason is ever added later).

    See _cleanup_ghost_customer() above for the customer-row cleanup this
    also performs.

    NOTE - overpayment on partial void: voiding one line of a multi-line
    transaction does NOT touch that transaction's sale_payments rows.
    If the customer already paid the full original total, the
    transaction's `total` (recomputed from only the remaining active
    lines) will now be less than what they paid - an overpayment that
    isn't reversed automatically here, since whether to hand cash back
    immediately vs. track it as a credit is a business decision, not a
    technical one. See CLAMP_OVERPAYMENT_TO_ZERO in
    app/services/customer_reports.py, which is the single switch that
    controls whether this overpayment stays invisible (today's default)
    or surfaces as a trackable refund-owed amount once you decide how you
    want it handled. Nothing needs to change here in sales.py either way.
    The same tradeoff applies unchanged to delete_sale_line() below.
    """
    with db_cursor(commit=True) as cur:
        sale = cur.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
        if not sale:
            raise ValueError("Sale not found")
        if sale["is_voided"]:
            return

        # Service lines never touched inventory on create — don't restore stock here.
        if not sale["service_description"]:
            product_row = cur.execute("SELECT name, quantity FROM products WHERE id = ?", (sale["product_id"],)).fetchone()
            cur.execute("UPDATE products SET quantity = quantity + ? WHERE id = ?",
                        (sale["quantity"], sale["product_id"]))
            if product_row:
                txn_id = sale["transaction_id"]
                product_audit.log_event(
                    sale["product_id"], product_row["name"], "quantity_manual",
                    quantity_before=product_row["quantity"], quantity_after=product_row["quantity"] + sale["quantity"],
                    new_value=str(sale["quantity"]),
                    reference=str(txn_id) if txn_id else None,
                    reference_type="transaction" if txn_id else None,
                    note="إلغاء بيع (void) — إرجاع الكمية للمخزون", cur=cur,
                )
                product_audit.record_expected_returns_snapshot(cur=cur)
        cur.execute("DELETE FROM warranties WHERE sale_id = ?", (sale_id,))
        cur.execute(
            "UPDATE sales SET is_voided = 1, voided_at = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), sale_id),
        )

        if sale["customer_id"]:
            _cleanup_ghost_customer(cur, sale["customer_id"])


def delete_sale_line(sale_id, quantity=None, cur=None):
    """حذف: permanently removes this sale line, in full or in part -
    unlike void_sale() this actually deletes data instead of flagging it.

    quantity: how many units to delete from this line. None (the default)
    deletes the whole line. A value smaller than the line's own quantity
    only shrinks sales.quantity and returns that many units to stock,
    leaving the row (and its warranty - not tracked per-unit, same as
    void_sale never splits it) in place. A value >= the line's quantity
    behaves the same as None: the row itself is removed, and its warranty
    cascades away automatically (schema: warranties.sale_id ON DELETE
    CASCADE).

    Stock is only restored for active (non-voided), tangible (non-service)
    lines - a voided line already gave its stock back when it was voided
    (restoring it again here would double-credit the shelf), and a
    service line never touched stock to begin with. Already-voided lines
    always delete in full regardless of the quantity argument, since a
    void already collapsed the line to "nothing outstanding".

    If this was the last line in its transaction, the transaction row is
    removed too (schema: sale_payments.transaction_id ON DELETE CASCADE
    takes care of its payments). See void_sale()'s docstring for the
    overpayment tradeoff this shares.

    Returns True if the row was fully removed, False if only shrunk.
    """
    if cur is None:
        with db_cursor(commit=True) as cur:
            return _delete_sale_line_core(sale_id, quantity, cur)
    return _delete_sale_line_core(sale_id, quantity, cur)


def _delete_sale_line_core(sale_id, quantity, cur):
    sale = cur.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
    if not sale:
        raise ValueError("Sale not found")

    full_quantity = sale["quantity"]
    was_voided = bool(sale["is_voided"])

    if quantity is None or was_voided:
        quantity = full_quantity
    else:
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            raise ValueError("الكمية يجب أن تكون رقمًا صحيحًا.")
        if quantity <= 0 or quantity > full_quantity:
            raise ValueError(f"اختر كمية بين 1 و {full_quantity}.")

    restore_stock = (not was_voided) and (not sale["service_description"])
    if restore_stock:
        product_row = cur.execute("SELECT name, quantity FROM products WHERE id = ?", (sale["product_id"],)).fetchone()
        cur.execute("UPDATE products SET quantity = quantity + ? WHERE id = ?",
                    (quantity, sale["product_id"]))
        if product_row:
            txn_id = sale["transaction_id"]
            product_audit.log_event(
                sale["product_id"], product_row["name"], "quantity_manual",
                quantity_before=product_row["quantity"], quantity_after=product_row["quantity"] + quantity,
                new_value=str(quantity),
                reference=str(txn_id) if txn_id else None,
                reference_type="transaction" if txn_id else None,
                note="حذف سطر بيع — إرجاع الكمية للمخزون", cur=cur,
            )
            product_audit.record_expected_returns_snapshot(cur=cur)

    fully_deleted = quantity >= full_quantity
    transaction_id = sale["transaction_id"]
    customer_id = sale["customer_id"]

    if fully_deleted:
        cur.execute("DELETE FROM sales WHERE id = ?", (sale_id,))
    else:
        cur.execute("UPDATE sales SET quantity = quantity - ? WHERE id = ?", (quantity, sale_id))

    if transaction_id and fully_deleted:
        remaining_lines = cur.execute(
            "SELECT COUNT(*) FROM sales WHERE transaction_id = ?", (transaction_id,)
        ).fetchone()[0]
        if remaining_lines == 0:
            cur.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))

    # Only re-check ghost status if this deletion actually changed the
    # customer's active-sale count (a partial shrink of an already-
    # active line doesn't - the line, and thus the customer, is still
    # active either way).
    if customer_id and fully_deleted and not was_voided:
        _cleanup_ghost_customer(cur, customer_id)

    return fully_deleted


def delete_transaction(transaction_id):
    """حذف on a whole receipt (Sales History page): every line in the
    transaction is removed via delete_sale_line() - same stock-restore,
    warranty-cascade, and ghost-customer cleanup as deleting one line at a
    time - and the transaction row disappears on its own once the last
    line is gone (see delete_sale_line()). This is intentionally executed
    in a single database transaction so a mid-delete failure cannot leave
    the database half-deleted."""
    with db_cursor(commit=True) as cur:
        rows = cur.execute(
            "SELECT id FROM sales WHERE transaction_id = ?", (transaction_id,)
        ).fetchall()
        if not rows:
            raise ValueError("الإيصال غير موجود أو محذوف بالفعل.")
        for row in rows:
            delete_sale_line(row["id"], cur=cur)


def list_sales(product_id=None, date_from=None, date_to=None, include_voided=False, query=None):
    sql = """
        SELECT s.*, COALESCE(NULLIF(TRIM(s.custom_product_name), ''), s.service_description, p.name) AS product_name
        FROM sales s JOIN products p ON p.id = s.product_id
        WHERE 1 = 1
    """
    params = []
    if not include_voided:
        sql += " AND s.is_voided = 0"
    if product_id:
        sql += " AND s.product_id = ?"
        params.append(product_id)
    if date_from:
        sql += " AND s.sale_date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND s.sale_date <= ?"
        params.append(date_to)
    if query:
        like_query = f"%{query}%"
        sql += (
            " AND (p.name LIKE ? OR s.customer_name LIKE ? OR s.customer_phone LIKE ?"
            " OR s.selling_price LIKE ? OR s.quantity LIKE ? OR s.sale_date LIKE ? )"
        )
        params.extend([like_query] * 6)
    sql += " ORDER BY s.sale_date DESC"

    with db_cursor() as cur:
        return [dict(r) for r in cur.execute(sql, params).fetchall()]


def customer_purchase_history(customer_id=None, customer_phone=None, customer_name=None):
    """Prefers customer_id (the reliable link) when given. Falls back to
    phone/name text matching for callers that don't have a customer_id
    handy - kept for backward compatibility, not used by new code."""
    if not customer_id and not customer_phone and not customer_name:
        return []
    query = """
        SELECT s.*, COALESCE(NULLIF(TRIM(s.custom_product_name), ''), s.service_description, p.name) AS product_name
        FROM sales s JOIN products p ON p.id = s.product_id
        WHERE s.is_voided = 0 AND
    """
    if customer_id:
        query += " s.customer_id = ?"
        params = [customer_id]
    elif customer_phone:
        query += " s.customer_phone = ?"
        params = [customer_phone]
    else:
        query += " s.customer_name = ?"
        params = [customer_name]
    query += " ORDER BY s.sale_date DESC"

    with db_cursor() as cur:
        return [dict(r) for r in cur.execute(query, params).fetchall()]


def get_sale_detail(sale_id):
    """Full detail for the Sales History click-through: the product (with
    images/specs), this sale's own record, and the customer's whole
    purchase history (every non-voided sale linked to the same customer,
    or - for older sales predating customer_id - sharing their phone)."""
    with db_cursor() as cur:
        sale = cur.execute(
            """SELECT s.*, COALESCE(NULLIF(TRIM(s.custom_product_name), ''), s.service_description, p.name) AS product_name,
                      p.grade, p.category_id
               FROM sales s JOIN products p ON p.id = s.product_id WHERE s.id = ?""",
            (sale_id,),
        ).fetchone()
        if not sale:
            return None
        sale = dict(sale)

        sale["images"] = [dict(r) for r in cur.execute(
            "SELECT * FROM product_images WHERE product_id = ? ORDER BY is_primary DESC, sort_order",
            (sale["product_id"],),
        ).fetchall()]
        sale["specifications"] = [dict(r) for r in cur.execute(
            """SELECT cf.field_label, spec.value FROM specifications spec
               JOIN category_fields cf ON cf.id = spec.category_field_id
               WHERE spec.product_id = ? ORDER BY cf.sort_order""",
            (sale["product_id"],),
        ).fetchall()]
        warranty = cur.execute("SELECT * FROM warranties WHERE sale_id = ?", (sale_id,)).fetchone()
        sale["warranty"] = dict(warranty) if warranty else None

        if sale["transaction_id"]:
            sale["payments"] = [dict(r) for r in cur.execute(
                "SELECT * FROM sale_payments WHERE transaction_id = ?", (sale["transaction_id"],),
            ).fetchall()]
            sale["transaction_lines"] = [dict(r) for r in cur.execute(
                """SELECT s2.*, COALESCE(NULLIF(TRIM(s2.custom_product_name), ''), s2.service_description, p2.name) AS product_name
                   FROM sales s2
                   JOIN products p2 ON p2.id = s2.product_id
                   WHERE s2.transaction_id = ? AND (s2.is_voided = 0 OR s2.id = ?) ORDER BY s2.id""",
                (sale["transaction_id"], sale_id),
            ).fetchall()]
        else:
            sale["payments"] = []
            sale["transaction_lines"] = [sale]

    sale["history"] = [
        h for h in customer_purchase_history(
            customer_id=sale.get("customer_id"),
            customer_phone=sale.get("customer_phone"),
            customer_name=sale.get("customer_name"),
        )
        if h["id"] != sale_id
    ]
    return sale


def get_transaction(transaction_id):
    with db_cursor() as cur:
        txn = cur.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
        if not txn:
            return None
        txn = dict(txn)
        txn["lines"] = [dict(r) for r in cur.execute(
            """SELECT s.*, COALESCE(NULLIF(TRIM(s.custom_product_name), ''), s.service_description, p.name) AS product_name
               FROM sales s
               JOIN products p ON p.id = s.product_id
               WHERE s.transaction_id = ? AND s.is_voided = 0 ORDER BY s.id""",
            (transaction_id,),
        ).fetchall()]
        txn["payments"] = [dict(r) for r in cur.execute(
            "SELECT * FROM sale_payments WHERE transaction_id = ?", (transaction_id,),
        ).fetchall()]

    txn["total"] = sum(l["selling_price"] * l["quantity"] for l in txn["lines"])
    txn["customer_name"] = txn["lines"][0]["customer_name"] if txn["lines"] else None
    txn["customer_phone"] = txn["lines"][0]["customer_phone"] if txn["lines"] else None
    txn["customer_id"] = txn["lines"][0]["customer_id"] if txn["lines"] else None
    paid = sum(p["amount"] for p in txn["payments"])
    txn["paid"] = paid
    txn["remaining"] = max(txn["total"] - paid, 0)
    txn["receipt_number"] = receipt_number(txn["id"], txn["created_at"], stored=txn.get("receipt_number"))
    return txn


def list_transactions(date_from=None, date_to=None, query=None):
    """One row per receipt/transaction for the Sales History page - the
    same grouping style the Customers page already uses per-customer
    (see customers._purchase_rows_for_customer), but across every
    transaction regardless of whether a customer is attached (walk-in
    sales have no customer_id).

    Voided lines are excluded from the totals (same convention as
    list_sales/get_transaction); a transaction whose every line was
    voided simply won't appear, matching how it already disappears from
    per-line history today."""
    sql = """
        SELECT
            t.id AS transaction_id,
            t.created_at,
            MAX(t.receipt_number) AS receipt_number,
            SUM(s.quantity) AS total_quantity,
            SUM(s.quantity * s.selling_price) AS total_amount,
            COALESCE((
                SELECT SUM(sp.amount) FROM sale_payments sp WHERE sp.transaction_id = t.id
            ), 0) AS paid_amount,
            MIN(s.customer_id) AS customer_id,
            MAX(s.customer_name) AS customer_name,
            MAX(s.customer_phone) AS customer_phone,
            GROUP_CONCAT(COALESCE(NULLIF(TRIM(s.custom_product_name), ''), s.service_description, p.name), ', ') AS product_names
        FROM transactions t
        JOIN sales s ON s.transaction_id = t.id AND s.is_voided = 0
        JOIN products p ON p.id = s.product_id
        WHERE 1 = 1
    """
    params = []
    if date_from:
        sql += " AND t.created_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND t.created_at <= ?"
        params.append(date_to)
    sql += " GROUP BY t.id ORDER BY t.created_at DESC, t.id DESC"

    with db_cursor() as cur:
        rows = [dict(r) for r in cur.execute(sql, params).fetchall()]

    txn_ids = [row["transaction_id"] for row in rows]
    payment_method_map = _payment_method_summary(txn_ids)
    order_map = _order_summary(txn_ids)

    for row in rows:
        order = order_map.get(row["transaction_id"])
        row["order_id"] = order["id"] if order else None
        row["order_status"] = order["status"] if order else None
        total_amount = float(row["total_amount"] or 0)
        paid_amount = float(row["paid_amount"] or 0)
        row["total_quantity"] = int(row["total_quantity"] or 0)
        row["total_amount"] = total_amount
        row["paid_amount"] = paid_amount
        row["remaining_amount"] = max(total_amount - paid_amount, 0)
        row["product_names"] = row["product_names"] or ""
        row["payment_method"] = payment_method_map.get(row["transaction_id"])
        row["receipt_number"] = receipt_number(row["transaction_id"], row["created_at"], stored=row.get("receipt_number"))

    if query:
        rows = _filter_transaction_rows(rows, query)

    return rows


def _payment_method_summary(transaction_ids):
    """{transaction_id: method} for transactions paid with a single
    method, or {transaction_id: 'mixed'} when split across several."""
    if not transaction_ids:
        return {}
    from collections import defaultdict
    placeholders = ",".join("?" for _ in transaction_ids)
    with db_cursor() as cur:
        rows = cur.execute(
            f"SELECT transaction_id, method FROM sale_payments WHERE transaction_id IN ({placeholders})",
            transaction_ids,
        ).fetchall()
    methods_by_txn = defaultdict(set)
    for r in rows:
        methods_by_txn[r["transaction_id"]].add(r["method"])
    return {
        tid: (next(iter(methods)) if len(methods) == 1 else "mixed")
        for tid, methods in methods_by_txn.items()
    }


def _order_summary(transaction_ids):
    """{transaction_id: {id, status}} for transactions that are delivery
    orders (نوع البيعه column on Sales History, and the جانب اوردر link).
    A transaction absent from this map is an ordinary sale."""
    if not transaction_ids:
        return {}
    placeholders = ",".join("?" for _ in transaction_ids)
    with db_cursor() as cur:
        rows = cur.execute(
            f"SELECT id, transaction_id, status FROM orders WHERE transaction_id IN ({placeholders})",
            transaction_ids,
        ).fetchall()
    return {r["transaction_id"]: {"id": r["id"], "status": r["status"]} for r in rows}


def _filter_transaction_rows(rows, query):
    q = query.strip().lower()
    if not q:
        return rows
    qnum = None
    try:
        qnum = float(q)
    except ValueError:
        pass

    matched = []
    for row in rows:
        if (q in (row["customer_name"] or "").lower()
                or q in (row["customer_phone"] or "").lower()
                or q in row["product_names"].lower()
                or q in (row["created_at"] or "").lower()
                or q in row["receipt_number"].lower()):
            matched.append(row)
            continue
        if qnum is not None and (
            abs(row["total_amount"] - qnum) < 0.01
            or abs(row["paid_amount"] - qnum) < 0.01
            or abs(row["remaining_amount"] - qnum) < 0.01
        ):
            matched.append(row)
    return matched
