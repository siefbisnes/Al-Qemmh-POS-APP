from datetime import datetime

from app.db import db_cursor
from app.services.sales import receipt_number


def _base_query():
    # LEFT JOIN (not JOIN) transactions: sales.transaction_id is nullable
    # per schema, so a warranty on a sale with no transaction must still
    # come back instead of silently disappearing from the list.
    # t.created_at is aliased - sales also has its own created_at column,
    # and an unaliased SELECT * would let one silently clobber the other.
    return """
        SELECT w.*, s.product_id, s.customer_name, s.customer_phone, s.sale_date,
               s.transaction_id, t.created_at AS transaction_created_at,
               p.name AS product_name
        FROM warranties w
        JOIN sales s ON s.id = w.sale_id
        JOIN products p ON p.id = s.product_id
        LEFT JOIN transactions t ON t.id = s.transaction_id
        WHERE 1 = 1
    """


def _apply_date_range(rows_query, params, date_from, date_to):
    """Date range applies to the sale date (when the item was actually
    sold), matching how date filters work everywhere else in the app
    (Reports, Sales History) - not the warranty's expiration date."""
    if date_from:
        rows_query += " AND s.sale_date >= ?"
        params.append(date_from)
    if date_to:
        rows_query += " AND s.sale_date <= ?"
        params.append(date_to)
    return rows_query, params


def _attach_receipt_number(row):
    """Every row gets its invoice/receipt number attached the same way
    the Sales History and receipt PDF do (see sales.receipt_number) -
    reusing that single canonical formula rather than recomputing it a
    second way, so this can never disagree with what's printed on the
    actual receipt. None if the sale has no transaction at all."""
    if row.get("transaction_id"):
        row["receipt_number"] = receipt_number(row["transaction_id"], row.get("transaction_created_at"))
    else:
        row["receipt_number"] = None
    return row


def _matches_search(row, search):
    """Invoice/receipt number, product name, or customer name - a match
    on any one of the three is enough. Case-insensitive, simple
    substring match (consistent with how search works elsewhere in the
    app, e.g. the dashboard's product search)."""
    if not search:
        return True
    needle = search.strip().lower()
    if not needle:
        return True
    haystacks = [row.get("receipt_number"), row.get("product_name"), row.get("customer_name")]
    return any(needle in (h or "").lower() for h in haystacks)


def _filtered(rows, search):
    rows = [_attach_receipt_number(r) for r in rows]
    if search:
        rows = [r for r in rows if _matches_search(r, search)]
    return rows


def list_active(search=None, date_from=None, date_to=None):
    now = datetime.now().isoformat(timespec="seconds")
    query = _base_query() + " AND w.expiration_date >= ?"
    params = [now]
    query, params = _apply_date_range(query, params, date_from, date_to)
    query += " ORDER BY w.expiration_date"
    with db_cursor() as cur:
        rows = cur.execute(query, params).fetchall()
    return _filtered([dict(r) for r in rows], search)


def list_expired(search=None, date_from=None, date_to=None):
    now = datetime.now().isoformat(timespec="seconds")
    query = _base_query() + " AND w.expiration_date < ?"
    params = [now]
    query, params = _apply_date_range(query, params, date_from, date_to)
    query += " ORDER BY w.expiration_date DESC"
    with db_cursor() as cur:
        rows = cur.execute(query, params).fetchall()
    return _filtered([dict(r) for r in rows], search)


def get(warranty_id):
    with db_cursor() as cur:
        row = cur.execute(_base_query() + " AND w.id = ?", (warranty_id,)).fetchone()
    return _attach_receipt_number(dict(row)) if row else None


def delete_warranty(warranty_id):
    """حذف on the Warranties page: standalone - removes only this
    warranty row. Does NOT touch سجل المبيعات, stock, or anything else;
    the underlying sale is left completely alone."""
    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM warranties WHERE id = ?", (warranty_id,))
