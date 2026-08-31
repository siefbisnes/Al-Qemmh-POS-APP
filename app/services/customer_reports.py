from app.db import db_cursor


def _money(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# --- Partial-void / overpayment policy --------------------------------------
# When a multi-line transaction is partially voided (see void_sale() in
# sales.py), that transaction's `total` drops (voided lines stop counting)
# but its recorded sale_payments amount does NOT shrink with it - so a
# customer can end up having "paid" more than the transaction is now worth.
# What should happen to that difference is a business decision (do you hand
# the cash back on the spot? track it as a credit against their next visit?),
# not something to guess at in code. This flag is the single switch point:
#
#   CLAMP_OVERPAYMENT_TO_ZERO = True   (current default, unchanged behavior)
#     Overpayment stays invisible. `remaining` never goes below zero, so an
#     overpaid customer just looks "fully paid, no debt". Fine as long as
#     you always settle the difference by hand (cash back) the moment you
#     void a line, and never need it tracked afterward.
#
#   CLAMP_OVERPAYMENT_TO_ZERO = False
#     Overpayment becomes visible instead of silently disappearing.
#     `remaining` can go negative for a transaction, and
#     customer_debt_summary() reports the total separately as
#     `total_credit` / `customers_owed_refund` rather than hiding it.
#     Switch to this once you've decided refunds owed to customers should
#     be tracked in the system instead of settled by hand every time.
#
# Flip this one flag when you're ready - nothing else needs to change.
CLAMP_OVERPAYMENT_TO_ZERO = True


def payment_totals(date_from=None, date_to=None):
    """Money received by payment date, not by sale date."""
    query = """
        SELECT sp.method, SUM(sp.amount) AS total
        FROM sale_payments sp
        WHERE EXISTS (
            SELECT 1
            FROM sales s
            WHERE s.transaction_id = sp.transaction_id
              AND s.is_voided = 0
        )
    """
    params = []
    if date_from:
        query += " AND sp.created_at >= ?"
        params.append(date_from)
    if date_to:
        query += " AND sp.created_at <= ?"
        params.append(date_to)
    query += " GROUP BY sp.method"

    with db_cursor() as cur:
        rows = cur.execute(query, params).fetchall()

    by_method = {row["method"]: _money(row["total"]) for row in rows}
    cash = by_method.get("cash", 0) or 0
    online = (
        (by_method.get("vodafone_cash", 0) or 0)
        + (by_method.get("instapay", 0) or 0)
        + (by_method.get("instabarid", 0) or 0)
    )
    return {
        "cash": cash,
        "online": online,
        "by_method": by_method,
        "total_received": cash + online,
    }


def customer_debt_summary():
    """Current outstanding customer debt, derived from active sales.

    Customer identity for de-duplication prefers customer_id (the same
    resolved-identity link used everywhere else in the app - voiding,
    purchase history, name normalization) over raw customer_name /
    customer_phone text. Falling back to text-only matching would double
    -count the same real customer if their name was typed slightly
    differently across visits with no phone to anchor it - exactly the
    ambiguity customer_id already exists to solve. Legacy sales that
    predate customer_id (customer_id IS NULL) still fall back to
    phone-then-name text matching.

    See CLAMP_OVERPAYMENT_TO_ZERO above for how a per-transaction
    overpayment (from a partial void after full payment) is handled.
    """
    query = """
        SELECT
            t.id AS transaction_id,
            MAX(s.customer_id) AS customer_id,
            MAX(NULLIF(TRIM(s.customer_name), '')) AS customer_name,
            MAX(NULLIF(TRIM(s.customer_phone), '')) AS customer_phone,
            SUM(s.quantity * s.selling_price) AS total_amount,
            COALESCE((
                SELECT SUM(sp.amount)
                FROM sale_payments sp
                WHERE sp.transaction_id = t.id
            ), 0) AS paid_amount
        FROM transactions t
        JOIN sales s ON s.transaction_id = t.id
        WHERE s.is_voided = 0
          AND (
                TRIM(COALESCE(s.customer_name, '')) <> ''
             OR TRIM(COALESCE(s.customer_phone, '')) <> ''
          )
        GROUP BY t.id
    """

    with db_cursor() as cur:
        rows = [dict(r) for r in cur.execute(query).fetchall()]

    total_invoiced = 0.0
    total_paid = 0.0
    total_remaining = 0.0
    total_credit = 0.0  # only ever non-zero when CLAMP_OVERPAYMENT_TO_ZERO is False
    debtors = set()
    credited = set()  # customers currently overpaid (only tracked when unclamped)
    customers = set()
    for row in rows:
        total = _money(row["total_amount"])
        paid = _money(row["paid_amount"])
        remaining_raw = total - paid

        remaining = max(remaining_raw, 0) if CLAMP_OVERPAYMENT_TO_ZERO else remaining_raw

        total_invoiced += total
        total_paid += paid
        total_remaining += max(remaining, 0)
        total_credit += max(-remaining, 0)

        # Prefer the resolved customer_id; only fall back to raw text for
        # legacy transactions that predate the customers table.
        customer_key = row["customer_id"] or row["customer_phone"] or row["customer_name"]
        if customer_key:
            customers.add(customer_key)
        if remaining > 0.009 and customer_key:
            debtors.add(customer_key)
        if remaining < -0.009 and customer_key:
            credited.add(customer_key)

    return {
        "customers": len(customers),
        "customers_with_debt": len(debtors),
        "customers_owed_refund": len(credited),
        "total_invoiced": total_invoiced,
        "total_paid": total_paid,
        "total_remaining": total_remaining,
        "total_credit": total_credit,
    }
