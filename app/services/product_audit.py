"""Product audit log + Expected Returns daily snapshot.

Every meaningful product-affecting mutation in app/services/products.py
calls log_event() right after its own change, and
record_expected_returns_snapshot() whenever quantity or selling_price
may have changed (both accept an optional `cur` so they can join an
already-open transaction instead of opening a second one on the same
request-scoped connection - see app/db.py:db_cursor/get_db, which
share one sqlite3 connection per request via `g.db`; a nested
`db_cursor(commit=True)` on that same connection would both commit the
caller's still-in-progress transaction early and, since the write lock
in app/db.py is not reentrant, deadlock).

If app/services/writeoffs.py records damaged-stock (هالك) write-offs
outside of products.py's own functions, it should call
log_event(..., event_type="quantity_damaged") and
record_expected_returns_snapshot() the same way after its own commit -
not available to instrument directly here, see the delivery note for
this feature.
"""
from datetime import datetime

from flask import session

from app.db import db_cursor


EVENT_LABELS_AR = {
    "created": "إضافة منتج",
    "removed": "حذف منتج",
    "quantity_sale": "بيع",
    "quantity_manual": "تغيير كمية يدوي",
    "quantity_damaged": "هالك",
    "price_selling": "تغيير سعر البيع",
    "price_purchase": "تغيير سعر الشراء",
    "name_changed": "تغيير اسم المنتج",
    "specs_changed": "تعديل مواصفات المنتج",
}


def _current_username():
    try:
        return session.get("username")
    except RuntimeError:
        # Called outside a request context (e.g. a one-off script) -
        # the audit row is still useful without a username attached.
        return None


def log_event(product_id, product_name, event_type, field=None, old_value=None,
               new_value=None, quantity_before=None, quantity_after=None,
               reference=None, reference_type=None, note=None, cur=None):
    """Writes one audit row. old_value/new_value are stringified since
    they're free-form across very different event types (a price, a
    name, a quantity delta) - see schema.sql for the full rationale.

    reference_type distinguishes what `reference` actually IS, so the
    audit log page can build a real link instead of showing a bare,
    unclickable id: pass reference_type="transaction" (with
    reference=transaction_id) for anything that happened as part of a
    specific sale, so it can link straight to that invoice. Leave both
    None for events that are really about the product itself (created,
    removed, price/name/specs changes, write-offs, manual adjustments)
    - those link via product_id to the product's own page instead."""
    def _to_text(v):
        return None if v is None else str(v)

    params = (
        product_id, product_name, event_type, field,
        _to_text(old_value), _to_text(new_value),
        quantity_before, quantity_after, reference, reference_type, note,
        _current_username(),
    )
    sql = """INSERT INTO product_audit_log
             (product_id, product_name, event_type, field, old_value, new_value,
              quantity_before, quantity_after, reference, reference_type, note, username)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

    if cur is not None:
        cur.execute(sql, params)
    else:
        with db_cursor(commit=True) as c:
            c.execute(sql, params)


def list_events(event_type=None, product_id=None, date_from=None, date_to=None,
                 page=1, page_size=50):
    """Newest first (created_at DESC, id DESC as a deterministic
    tiebreaker for same-timestamp rows). Filters are optional and
    AND-combined. Returns {"events": [...], "has_more": bool, "page": int}."""
    query = "SELECT * FROM product_audit_log WHERE 1 = 1"
    params = []
    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)
    if product_id:
        query += " AND product_id = ?"
        params.append(product_id)
    if date_from:
        query += " AND created_at >= ?"
        params.append(date_from)
    if date_to:
        query += " AND created_at <= ?"
        params.append(date_to)
    query += " ORDER BY created_at DESC, id DESC"

    page = max(1, page)
    offset = (page - 1) * page_size
    query += " LIMIT ? OFFSET ?"
    params.extend([page_size + 1, offset])  # fetch one extra row to detect "more pages"

    with db_cursor() as cur:
        rows = [dict(r) for r in cur.execute(query, params).fetchall()]

    has_more = len(rows) > page_size
    events = rows[:page_size]
    _attach_invoice_numbers(events)
    return {"events": events, "has_more": has_more, "page": page}


def _attach_invoice_numbers(events):
    """For any event referencing a specific sale (reference_type ==
    "transaction"), attaches events[i]["invoice_number"] using the SAME
    canonical receipt number shown on Sales History, the transaction
    detail page, and the printed receipt
    (app/services/sales.py:receipt_number) - never the raw
    transaction_id on its own, which just looks like a meaningless
    truncated number to a shop owner. Imported lazily to avoid a
    circular import - sales.py already imports this module."""
    txn_ids = {
        int(e["reference"]) for e in events
        if e.get("reference_type") == "transaction" and e.get("reference")
    }
    if not txn_ids:
        return

    from app.services.sales import receipt_number as _receipt_number

    placeholders = ",".join("?" for _ in txn_ids)
    with db_cursor() as cur:
        rows = cur.execute(
            f"SELECT id, created_at, receipt_number FROM transactions WHERE id IN ({placeholders})",
            list(txn_ids),
        ).fetchall()
    txn_info_by_id = {r["id"]: (r["created_at"], r["receipt_number"]) for r in rows}

    for e in events:
        if e.get("reference_type") == "transaction" and e.get("reference"):
            txn_id = int(e["reference"])
            created_at, stored = txn_info_by_id.get(txn_id, (None, None))
            e["invoice_number"] = _receipt_number(txn_id, created_at, stored=stored)


# ---------- Expected Returns daily snapshot ----------

def record_expected_returns_snapshot(cur=None):
    """Upserts TODAY's Expected Returns value:
    Σ(quantity × selling_price) over every currently-sellable product -
    same population/formula as owner_dashboard.purchases_vs_expected's
    live figure. Safe to call more than once per day; each call
    overwrites today's row with the current true value, so the LAST
    call on a given day is what that day's history ends up showing."""
    def _run(c):
        row = c.execute(
            """SELECT COALESCE(SUM(quantity * selling_price), 0) AS total
               FROM products
               WHERE COALESCE(is_active, 1) = 1
                 AND COALESCE(source, '') <> 'service_placeholder'"""
        ).fetchone()
        value = float(row["total"] or 0)
        today = datetime.now().strftime("%Y-%m-%d")
        c.execute(
            """INSERT INTO expected_returns_daily (snapshot_date, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(snapshot_date) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (today, value),
        )

    if cur is not None:
        _run(cur)
    else:
        with db_cursor(commit=True) as c:
            _run(c)


def expected_returns_history(date_from=None, date_to=None):
    """[{"date": "YYYY-MM-DD", "value": float}, ...] ordered oldest to
    newest, restricted to rows that actually exist - i.e. from the day
    this feature was first recorded onward. No fabricated rows for
    dates before that."""
    query = "SELECT snapshot_date, value FROM expected_returns_daily WHERE 1 = 1"
    params = []
    if date_from:
        query += " AND snapshot_date >= ?"
        params.append(date_from[:10])
    if date_to:
        query += " AND snapshot_date <= ?"
        params.append(date_to[:10])
    query += " ORDER BY snapshot_date"
    with db_cursor() as cur:
        rows = cur.execute(query, params).fetchall()
    return [{"date": r["snapshot_date"], "value": float(r["value"] or 0)} for r in rows]
