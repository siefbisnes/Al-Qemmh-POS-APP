from app.db import db_cursor


def list_expenses(date_from=None, date_to=None):
    query = "SELECT * FROM expenses WHERE 1 = 1"
    params = []
    if date_from:
        query += " AND expense_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND expense_date <= ?"
        params.append(date_to)
    query += " ORDER BY expense_date DESC"

    with db_cursor() as cur:
        return [dict(r) for r in cur.execute(query, params).fetchall()]


def total_expenses(date_from=None, date_to=None):
    query = "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE 1 = 1"
    params = []
    if date_from:
        query += " AND expense_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND expense_date <= ?"
        params.append(date_to)

    with db_cursor() as cur:
        return cur.execute(query, params).fetchone()["total"]


def expenses_by_method(date_from=None, date_to=None):
    query = "SELECT payment_method, COALESCE(SUM(amount), 0) AS total FROM expenses WHERE 1 = 1"
    params = []
    if date_from:
        query += " AND expense_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND expense_date <= ?"
        params.append(date_to)
    query += " GROUP BY payment_method"

    with db_cursor() as cur:
        rows = cur.execute(query, params).fetchall()

    by_method = {row["payment_method"]: row["total"] for row in rows}
    cash = by_method.get("cash", 0) or 0
    online = (by_method.get("vodafone_cash", 0) or 0) + (by_method.get("instapay", 0) or 0)
    return {"cash": cash, "online": online, "by_method": by_method}


def add_expense(description, amount, expense_date=None, payment_method="cash"):
    if payment_method not in {"cash", "vodafone_cash", "instapay"}:
        raise ValueError("Invalid payment method.")
    if not expense_date:
        from datetime import datetime
        expense_date = datetime.now().isoformat(sep=" ", timespec="seconds")
    with db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO expenses (description, amount, payment_method, expense_date) VALUES (?, ?, ?, ?)",
            (description, amount, payment_method, expense_date),
        )
        return cur.lastrowid


def delete_expense(expense_id):
    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
