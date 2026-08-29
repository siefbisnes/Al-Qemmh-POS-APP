"""
هالك (stock write-offs) — liquidity / damage losses with a full ledger.

When the owner marks units as هالك on a product page:
  1. Snapshot purchase_price + selling_price
  2. cost_loss    = qty × purchase_price   (capital lost)
  3. revenue_loss = qty × selling_price    (forgone sell-through)
  4. Decrement products.quantity
  5. Domino: inventory value ↓, stagnant recalculates, owner dashboard's
     combined "راكد + هالك" chart reads this table, Net Profit subtracts
     cost_loss, and the financial ledger lists each write-off as its own
     line item
"""
from datetime import datetime

from app.db import db_cursor
from app.services import product_audit

# --- Deferred spoilage expense (12-month spread) -------------------------
# CUSTOM CLIENT-REQUESTED OVERRIDE of standard accounting: a write-off's
# full cost is normally expensed in the month it happens. Per client
# request, it is instead spread evenly across this many consecutive
# calendar months and posted to Net Profit one installment at a time. See
# docs/spoilage_management_requirements.md.
SPOILAGE_AMORTIZATION_MONTHS = 12


def _add_months(year, month, offset):
    """month is 1-12. Returns (year, month) offset by `offset` months,
    rolling over the year boundary as needed."""
    total = (year * 12 + (month - 1)) + offset
    return total // 12, (total % 12) + 1


def _generate_amortization_schedule(cur, writeoff_id, product_id, total_cost, writeoff_date):
    """Inserts SPOILAGE_AMORTIZATION_MONTHS rows into spoilage_amortization
    for this write-off, one per consecutive calendar month starting with
    writeoff_date's own month. All rows are generated up front - there is
    deliberately no row for month 13+, so a write-off's effect on Net
    Profit stops after its last installment by construction (no row to
    sum), not by a runtime "is it still within N months" date check.

    Rounding: the first (N-1) installments are amount/N rounded to 2dp;
    the final installment absorbs whatever remainder is left, so the N
    installments always sum to EXACTLY total_cost - never more or less.
    """
    n = SPOILAGE_AMORTIZATION_MONTHS
    start = (writeoff_date or "")[:10]
    try:
        year, month = int(start[:4]), int(start[5:7])
    except (ValueError, IndexError):
        now = datetime.now()
        year, month = now.year, now.month

    base_installment = round(total_cost / n, 2)
    running_total = 0.0
    for i in range(n):
        installment_number = i + 1
        if installment_number < n:
            amount = base_installment
            running_total += amount
        else:
            # Last installment absorbs the rounding remainder so the
            # schedule always sums to exactly total_cost.
            amount = round(total_cost - running_total, 2)
        y, m = _add_months(year, month, i)
        period = f"{y:04d}-{m:02d}"
        cur.execute(
            """
            INSERT INTO spoilage_amortization
                (writeoff_id, product_id, installment_number, period, amount)
            VALUES (?, ?, ?, ?, ?)
            """,
            (writeoff_id, product_id, installment_number, period, amount),
        )


def create_writeoff(product_id, quantity, note=None, writeoff_date=None):
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ValueError("الكمية يجب أن تكون رقمًا صحيحًا.")
    if quantity <= 0:
        raise ValueError("الكمية يجب أن تكون أكبر من صفر.")

    if not writeoff_date:
        writeoff_date = datetime.now().isoformat(timespec="seconds")
    note = (note or "").strip() or None

    with db_cursor(commit=True) as cur:
        row = cur.execute(
            """
            SELECT id, name, quantity, purchase_price, selling_price, is_active, source
            FROM products WHERE id = ?
            """,
            (product_id,),
        ).fetchone()
        if row is None:
            raise ValueError("المنتج غير موجود.")
        if not row["is_active"]:
            raise ValueError("لا يمكن تسجيل هالك لمنتج غير نشط.")
        if (row["source"] or "") == "service_placeholder":
            raise ValueError("لا يمكن تسجيل هالك لمنتج خدمي.")
        if quantity > row["quantity"]:
            raise ValueError(
                f"لا يمكن تسجيل {quantity} وحدة هالك، الموجود بالمخزون {row['quantity']} فقط."
            )

        purchase_price = float(row["purchase_price"] or 0)
        selling_price = float(row["selling_price"] or 0)
        cost_loss = purchase_price * quantity
        revenue_loss = selling_price * quantity

        cur.execute(
            """
            INSERT INTO stock_writeoffs
                (product_id, quantity, purchase_price, selling_price,
                 cost_loss, revenue_loss, note, writeoff_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id, quantity, purchase_price, selling_price,
                cost_loss, revenue_loss, note, writeoff_date,
            ),
        )
        writeoff_id = cur.lastrowid
        _generate_amortization_schedule(cur, writeoff_id, product_id, cost_loss, writeoff_date)
        cur.execute(
            "UPDATE products SET quantity = quantity - ? WHERE id = ?",
            (quantity, product_id),
        )
        product_audit.log_event(
            product_id, row["name"], "quantity_damaged",
            quantity_before=row["quantity"], quantity_after=row["quantity"] - quantity,
            new_value=str(quantity), reference=str(writeoff_id),
            note=note or f"خسارة تكلفة {cost_loss:.2f} ج.م · قيمة بيع ضائعة {revenue_loss:.2f} ج.م",
            cur=cur,
        )
        product_audit.record_expected_returns_snapshot(cur=cur)

    return {
        "id": writeoff_id,
        "product_id": product_id,
        "product_name": row["name"],
        "quantity": quantity,
        "purchase_price": purchase_price,
        "selling_price": selling_price,
        "cost_loss": round(cost_loss, 2),
        "revenue_loss": round(revenue_loss, 2),
        "writeoff_date": writeoff_date,
    }


def delete_writeoff(writeoff_id):
    """حذف on a هالك ledger entry (Reports page): reverses it - the
    written-off quantity goes back to products.quantity (mirroring how
    void_sale/delete_sale_line give stock back before removing their own
    row) - then the row itself is deleted. This does NOT undo whatever
    caused the write-off in the first place, only the bookkeeping entry
    and the stock count."""
    with db_cursor(commit=True) as cur:
        row = cur.execute("SELECT * FROM stock_writeoffs WHERE id = ?", (writeoff_id,)).fetchone()
        if not row:
            raise ValueError("سجل الهالك غير موجود.")
        cur.execute(
            "UPDATE products SET quantity = quantity + ? WHERE id = ?",
            (row["quantity"], row["product_id"]),
        )
        cur.execute("DELETE FROM stock_writeoffs WHERE id = ?", (writeoff_id,))
        product_row = cur.execute(
            "SELECT name, quantity FROM products WHERE id = ?", (row["product_id"],)
        ).fetchone()
        if product_row:
            product_audit.log_event(
                row["product_id"], product_row["name"], "quantity_manual",
                quantity_before=product_row["quantity"] - row["quantity"],
                quantity_after=product_row["quantity"],
                new_value=str(row["quantity"]), reference=str(writeoff_id),
                note="إلغاء سجل هالك — إرجاع الكمية للمخزون", cur=cur,
            )
            product_audit.record_expected_returns_snapshot(cur=cur)


def list_for_product(product_id):
    with db_cursor() as cur:
        rows = cur.execute(
            """
            SELECT * FROM stock_writeoffs
            WHERE product_id = ?
            ORDER BY writeoff_date DESC, id DESC
            """,
            (product_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def totals(date_from=None, date_to=None):
    """Aggregated write-off losses (cost = capital lost, revenue = forgone)."""
    query = """
        SELECT
            COALESCE(SUM(quantity), 0) AS units,
            COALESCE(SUM(cost_loss), 0) AS cost_loss,
            COALESCE(SUM(revenue_loss), 0) AS revenue_loss,
            COUNT(*) AS count
        FROM stock_writeoffs
        WHERE 1 = 1
    """
    params = []
    if date_from:
        query += " AND writeoff_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND writeoff_date <= ?"
        params.append(date_to)
    with db_cursor() as cur:
        row = cur.execute(query, params).fetchone()
    return {
        "units": row["units"],
        "cost_loss": float(row["cost_loss"] or 0),
        "revenue_loss": float(row["revenue_loss"] or 0),
        "count": row["count"],
    }


def amortized_total(date_from=None, date_to=None):
    """Sum of spoilage_amortization installments whose `period` falls
    within [date_from, date_to] - i.e. the actual Net Profit impact of
    ALL write-offs (regardless of when they happened) for this range, per
    the 12-month spread. Use this instead of totals()["cost_loss"] for
    Net Profit; keep totals()["cost_loss"] for "how much capital is
    currently tied up in damaged stock" (at-risk liquidity), which is a
    different question - see docs/spoilage_management_requirements.md §5.2.
    date_from/date_to may be full 'YYYY-MM-DD[...]' values (as used
    elsewhere in this module) or bare 'YYYY-MM' - only the first 7 chars
    are compared against `period`, which is always 'YYYY-MM'.
    """
    query = "SELECT COALESCE(SUM(amount), 0) AS total FROM spoilage_amortization WHERE 1 = 1"
    params = []
    if date_from:
        query += " AND period >= ?"
        params.append(date_from[:7])
    if date_to:
        query += " AND period <= ?"
        params.append(date_to[:7])
    with db_cursor() as cur:
        return float(cur.execute(query, params).fetchone()["total"] or 0)


def amortization_by_bucket(date_from, date_to, bucket):
    """{period_key: amount} of spoilage_amortization installments due in
    range - the Net Profit series' per-bucket replacement for
    totals_by_bucket() (which sums the FULL cost_loss into a single
    bucket instead of spreading it). `bucket` is accepted for interface
    parity with totals_by_bucket()/_sum_table_by_bucket() but installments
    are always monthly ('YYYY-MM') by design (see
    SPOILAGE_AMORTIZATION_MONTHS) - when the caller's bucket is 'day',
    each installment's full amount is attributed to the 1st of its month,
    since a day-level installment schedule isn't part of this feature.
    """
    query = """
        SELECT period, COALESCE(SUM(amount), 0) AS total
        FROM spoilage_amortization
        WHERE period >= ? AND period <= ?
        GROUP BY period
    """
    with db_cursor() as cur:
        rows = cur.execute(query, (date_from[:7], date_to[:7])).fetchall()

    data = {}
    for r in rows:
        period = r["period"]
        key = period if bucket == "month" else f"{period}-01"
        data[key] = data.get(key, 0.0) + float(r["total"] or 0)
    return data


def totals_by_bucket(date_from, date_to, bucket):
    """{bucket_key: cost_loss} for net-profit time series."""
    data = {}
    query = "SELECT writeoff_date, cost_loss FROM stock_writeoffs WHERE writeoff_date >= ? AND writeoff_date <= ?"
    with db_cursor() as cur:
        rows = cur.execute(query, (date_from, date_to)).fetchall()
    for r in rows:
        d = (r["writeoff_date"] or "")[:10]
        key = d[:7] if bucket == "month" and len(d) >= 7 else (d or "unknown")
        data[key] = data.get(key, 0.0) + float(r["cost_loss"] or 0)
    return data


def recent_items(limit=25, date_from=None, date_to=None):
    """Capped list for UI panels (product detail history, dashboard
    "recent damaged" list). NOT suitable for the financial ledger, which
    needs every row - use list_all() there."""
    query = """
        SELECT w.*, p.name AS product_name
        FROM stock_writeoffs w
        JOIN products p ON p.id = w.product_id
        WHERE 1 = 1
    """
    params = []
    if date_from:
        query += " AND w.writeoff_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND w.writeoff_date <= ?"
        params.append(date_to)
    query += " ORDER BY w.writeoff_date DESC, w.id DESC LIMIT ?"
    params.append(limit)
    with db_cursor() as cur:
        return [dict(r) for r in cur.execute(query, params).fetchall()]


def list_all(date_from=None, date_to=None):
    """Every write-off in range, uncapped - for the financial ledger,
    which needs to show each one as its own line item (it previously
    listed Sales/Purchases/Expenses/manual adjustments but silently
    omitted write-offs, even though they reduce net profit everywhere
    else in the app)."""
    query = """
        SELECT w.*, p.name AS product_name
        FROM stock_writeoffs w
        JOIN products p ON p.id = w.product_id
        WHERE 1 = 1
    """
    params = []
    if date_from:
        query += " AND w.writeoff_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND w.writeoff_date <= ?"
        params.append(date_to)
    query += " ORDER BY w.writeoff_date DESC, w.id DESC"
    with db_cursor() as cur:
        return [dict(r) for r in cur.execute(query, params).fetchall()]
