"""
Manual corrections to any of the 6 Reports cards - e.g. the physical
cash drawer doesn't match the calculated total, or an expense/purchase
was paid outside the app and needs to be reflected. Each adjustment is
signed (positive = money added to that card, negative = removed) and
tagged with which card it affects, so every calculation on the Reports
page can just add SUM(amount) for its own target into its formula.
"""
from app.db import db_cursor

TARGETS = [
    ("drawer", "الدرج"),
    ("today", "اليوم"),
    ("online", "أونلاين"),
    ("total", "الاجمالي"),
    ("expenses", "المصروفات"),
    ("purchases", "المشتريات"),
    ("net_profit", "صافي الربح"),
]
_VALID_TARGETS = {key for key, _ in TARGETS}


def add_adjustment(target, amount, note=None):
    if target not in _VALID_TARGETS:
        raise ValueError("Invalid target card.")
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise ValueError("Amount must be a number.")
    if amount == 0:
        raise ValueError("Amount cannot be zero.")
    with db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO manual_adjustments (target, amount, note) VALUES (?, ?, ?)",
            (target, amount, (note or "").strip() or None),
        )
        return cur.lastrowid


def delete_adjustment(adjustment_id):
    """حذف on a manual-adjustment ledger entry (Reports page). Nothing to
    reverse beyond the row itself - an adjustment is already a manual
    correction with no downstream stock/warranty/customer side effects,
    unlike sale/write-off deletion."""
    with db_cursor(commit=True) as cur:
        row = cur.execute("SELECT id FROM manual_adjustments WHERE id = ?", (adjustment_id,)).fetchone()
        if not row:
            raise ValueError("التعديل غير موجود.")
        cur.execute("DELETE FROM manual_adjustments WHERE id = ?", (adjustment_id,))


def _normalize_datetime_filter(value, end_of_day=False):
    if not value or not isinstance(value, str):
        return value
    if len(value) == 10:
        return value + (" 23:59:59" if end_of_day else " 00:00:00")
    if len(value) >= 19 and value[10] == "T":
        return value.replace("T", " ", 1)
    return value


def adjustment_total(target, date_from=None, date_to=None):
    date_from = _normalize_datetime_filter(date_from)
    date_to = _normalize_datetime_filter(date_to, end_of_day=True)

    query = "SELECT COALESCE(SUM(amount), 0) AS total FROM manual_adjustments WHERE target = ?"
    params = [target]
    if date_from:
        query += " AND adjustment_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND adjustment_date <= ?"
        params.append(date_to)
    with db_cursor() as cur:
        return cur.execute(query, params).fetchone()["total"]


def list_adjustments(date_from=None, date_to=None):
    date_from = _normalize_datetime_filter(date_from)
    date_to = _normalize_datetime_filter(date_to, end_of_day=True)

    query = "SELECT * FROM manual_adjustments WHERE 1 = 1"
    params = []
    if date_from:
        query += " AND adjustment_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND adjustment_date <= ?"
        params.append(date_to)
    query += " ORDER BY adjustment_date DESC"
    with db_cursor() as cur:
        return [dict(r) for r in cur.execute(query, params).fetchall()]
