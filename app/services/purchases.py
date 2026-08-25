"""
A purchase is a general cost outflow - stock restocking, rent, utilities,
anything paid from the shop's cash or online balance. It's intentionally
NOT tied to a product row, since a lot of what gets logged here (rent,
electricity) has no product at all.

payment_method reuses the same three options Sales already defines, so
Reports can net purchases against sales on the same Cash/Online split
instead of introducing a second, inconsistent set of payment types.
"""
from app.db import db_cursor
from app.services.sales import PAYMENT_METHODS

_VALID_METHODS = {value for value, _ in PAYMENT_METHODS}


def create_purchase(name, cost, payment_method="cash", purchase_date=None):
    name = (name or "").strip()
    if not name:
        raise ValueError("Enter a name for this purchase.")
    try:
        cost = float(cost)
    except (TypeError, ValueError):
        raise ValueError("Cost must be a number.")
    if cost <= 0:
        raise ValueError("Cost must be greater than zero.")
    if payment_method not in _VALID_METHODS:
        raise ValueError("Invalid payment method.")

    with db_cursor(commit=True) as cur:
        if purchase_date:
            cur.execute(
                "INSERT INTO purchases (name, cost, payment_method, purchase_date) VALUES (?, ?, ?, ?)",
                (name, cost, payment_method, purchase_date),
            )
        else:
            cur.execute(
                "INSERT INTO purchases (name, cost, payment_method) VALUES (?, ?, ?)",
                (name, cost, payment_method),
            )
        return cur.lastrowid


def list_purchases(date_from=None, date_to=None):
    query = "SELECT * FROM purchases WHERE 1 = 1"
    params = []
    if date_from:
        query += " AND purchase_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND purchase_date <= ?"
        params.append(date_to)
    query += " ORDER BY purchase_date DESC"
    with db_cursor() as cur:
        return [dict(r) for r in cur.execute(query, params).fetchall()]


def total_purchases(date_from=None, date_to=None):
    query = "SELECT COALESCE(SUM(cost), 0) AS total FROM purchases WHERE 1 = 1"
    params = []
    if date_from:
        query += " AND purchase_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND purchase_date <= ?"
        params.append(date_to)
    with db_cursor() as cur:
        return cur.execute(query, params).fetchone()["total"]


def purchases_by_method(date_from=None, date_to=None):
    """Splits purchase cost by how it was paid, so Reports can subtract
    cash purchases from the Cash field and Vodafone Cash / Instapay
    purchases from the Online field - money leaving the same balance
    it would otherwise look like the shop still has."""
    query = "SELECT payment_method, COALESCE(SUM(cost), 0) AS total FROM purchases WHERE 1 = 1"
    params = []
    if date_from:
        query += " AND purchase_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND purchase_date <= ?"
        params.append(date_to)
    query += " GROUP BY payment_method"
    with db_cursor() as cur:
        rows = cur.execute(query, params).fetchall()
    by_method = {row["payment_method"]: row["total"] for row in rows}
    cash = by_method.get("cash", 0) or 0
    online = (by_method.get("vodafone_cash", 0) or 0) + (by_method.get("instapay", 0) or 0)
    return {"cash": cash, "online": online, "by_method": by_method}


def delete_purchase(purchase_id):
    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM purchases WHERE id = ?", (purchase_id,))
