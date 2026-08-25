from app.db import db_cursor
from app.services import customer_reports as customer_report_service


def summary(date_from=None, date_to=None):
    """Total revenue, cost, profit for sales in the given window (or all-time).
    Voided sales never count - that's the whole point of voiding instead of
    just deleting the row."""
    query = """SELECT s.quantity, s.selling_price, p.purchase_price FROM sales s
               JOIN products p ON p.id = s.product_id WHERE s.is_voided = 0"""
    params = []
    if date_from:
        query += " AND s.sale_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND s.sale_date <= ?"
        params.append(date_to)
    with db_cursor() as cur:
        rows = cur.execute(query, params).fetchall()
    revenue = sum(r["selling_price"] * r["quantity"] for r in rows)
    cost = sum(r["purchase_price"] * r["quantity"] for r in rows)
    return {
        "total_revenue": revenue,
        "total_cost": cost,
        "total_profit": revenue - cost,
        "units_sold": sum(r["quantity"] for r in rows),
    }


def payment_totals(date_from=None, date_to=None):
    return customer_report_service.payment_totals(date_from=date_from, date_to=date_to)


def today_report(date_from=None, all_time_if_none=False):
    """تقرير اليوم - everything sold today, plus the drawer/today figures
    that feed the top two Reports cards.

    If date_from is provided, the totals start from that timestamp.
    This is used when reports were reset during the current day.

    If date_from is None and all_time_if_none is True, no date filtering
    is applied at all (true all-time totals) instead of defaulting to
    "today". This is what the Reports page drawer card needs when
    "Reset Reports" has never been used - it should accumulate forever,
    not silently reset every midnight."""
    from datetime import date as _date
    from app.services import expenses as expense_service
    from app.services import purchases as purchase_service
    from app.services import adjustments as adjustment_service
    from app.services import writeoffs as writeoff_service

    if date_from:
        sale_query = """SELECT s.*, COALESCE(NULLIF(TRIM(s.custom_product_name), ''), s.service_description, p.name) AS product_name,
                      p.purchase_price, p.quantity AS remaining_quantity
               FROM sales s JOIN products p ON p.id = s.product_id
               WHERE s.is_voided = 0 AND s.sale_date >= ?
               ORDER BY s.sale_date"""
        sale_params = (date_from,)
        payments_from = date_from
        expenses_from = date_from
        purchases_from = date_from
        drawer_adj_from = date_from
        today_adj_from = date_from
        writeoff_date_from = date_from
        writeoff_date_to = None
        report_date = date_from
    elif all_time_if_none:
        sale_query = """SELECT s.*, COALESCE(NULLIF(TRIM(s.custom_product_name), ''), s.service_description, p.name) AS product_name,
                      p.purchase_price, p.quantity AS remaining_quantity
               FROM sales s JOIN products p ON p.id = s.product_id
               WHERE s.is_voided = 0
               ORDER BY s.sale_date"""
        sale_params = ()
        payments_from = None
        expenses_from = None
        purchases_from = None
        drawer_adj_from = None
        today_adj_from = None
        writeoff_date_from = None
        writeoff_date_to = None
        report_date = None
    else:
        today_str = _date.today().isoformat()
        sale_query = """SELECT s.*, COALESCE(NULLIF(TRIM(s.custom_product_name), ''), s.service_description, p.name) AS product_name,
                      p.purchase_price, p.quantity AS remaining_quantity
               FROM sales s JOIN products p ON p.id = s.product_id
               WHERE s.is_voided = 0 AND s.sale_date LIKE ?
               ORDER BY s.sale_date"""
        sale_params = (f"{today_str}%",)
        payments_from = today_str
        expenses_from = today_str
        purchases_from = today_str
        drawer_adj_from = today_str
        today_adj_from = today_str
        writeoff_date_from = today_str
        writeoff_date_to = f"{today_str}T23:59:59"
        report_date = today_str

    with db_cursor() as cur:
        rows = cur.execute(sale_query, sale_params).fetchall()
    sales_today = [dict(r) for r in rows]
    total_revenue = sum(s["selling_price"] * s["quantity"] for s in sales_today)
    total_profit = sum((s["selling_price"] - s["purchase_price"]) * s["quantity"] for s in sales_today)
    units_sold = sum(s["quantity"] for s in sales_today)
    sold_by_product = {}
    for s in sales_today:
        sold_by_product[s["product_name"]] = sold_by_product.get(s["product_name"], 0) + s["quantity"]
    sold_summary = [{"product_name": k, "quantity": v} for k, v in sold_by_product.items()]

    payments = payment_totals(date_from=payments_from)
    today_expenses = expense_service.expenses_by_method(date_from=expenses_from)
    today_expenses_total = expense_service.total_expenses(date_from=expenses_from)
    today_purchases = purchase_service.purchases_by_method(date_from=purchases_from)

    # هالك for this same window. Previously total_profit ignored write-offs
    # entirely, so recording a write-off "today" didn't move today's profit
    # figure even though it reduces net profit everywhere else in the app
    # (Owner Dashboard, Reports range summary). Subtracting it here brings
    # this figure in line with those.
    today_writeoff_cost = writeoff_service.totals(
        date_from=writeoff_date_from, date_to=writeoff_date_to
    )["cost_loss"]
    total_profit -= today_writeoff_cost

    drawer_adj = adjustment_service.adjustment_total("drawer", date_from=drawer_adj_from)
    today_adj = adjustment_service.adjustment_total("today", date_from=today_adj_from)
    online_adj = adjustment_service.adjustment_total("online", date_from=drawer_adj_from)

    drawer = payments["cash"] - today_expenses["cash"] - today_purchases["cash"] + drawer_adj
    online = payments["online"] - today_expenses["online"] - today_purchases["online"] + online_adj
    today_total = total_revenue + today_adj

    return {
        "date": report_date,
        "sales": sales_today,
        "sold_summary": sold_summary,
        "units_sold": units_sold,
        "total_revenue": total_revenue,
        "today_total": today_total,
        "total_profit": total_profit,
        "writeoff_cost": today_writeoff_cost,
        "count": len(sales_today),
        "payments": payments,
        "expenses": today_expenses,
        "expenses_total": today_expenses_total,
        "purchases": today_purchases,
        "drawer": drawer,
        "online": online,
    }


def today_profit():
    return today_report()["total_profit"]


def date_range_summary(date_from=None, date_to=None):
    """Cards 3-6: الاجمالي / المصروفات / المشتريات / صافي ربح.

    Defaults to all-time when no date filter is applied, matching the
    date toolbar already on the Reports page.

    Accounting Fix (Customer Debt):
    ================================
    A sale is only "realized" when the customer has actually paid for it.
    Until payment is collected, unpaid amounts are outstanding debt and must
    NOT be included in realized net profit.

    Two profit metrics are now calculated:
    
    1. realized_net_profit: Profit from money that has actually been collected.
       This is the "true" current profit figure, calculated as:
       Realized Revenue - Realized COGS - Expenses - Writeoff cost (+ manual adj)
       
       Where Realized Revenue = sum of payments actually received per transaction
       And Realized COGS = COGS proportion of the paid amount only.

    2. potential_net_profit: What the realized profit would become if all
       currently outstanding customer debts were eventually collected:
       Total Revenue - Total COGS - Expenses - Writeoff cost (+ manual adj)

    This allows distinguishing between:
    - Money already in hand (realized profit)
    - Money still owed by customers (outstanding debt)

    COGS (cost of only the units actually sold in the period) is the
    correct accounting basis - "Purchases" (all money spent restocking,
    sold or not) is a separate cash-outflow figure, still shown as its
    own card below, just no longer folded into net_profit.
    """
    from app.services import expenses as expense_service
    from app.services import purchases as purchase_service
    from app.services import adjustments as adjustment_service
    from app.services import writeoffs as writeoff_service

    # Total revenue and COGS (all sales, including unpaid)
    query = """SELECT s.quantity, s.selling_price, p.purchase_price FROM sales s
               JOIN products p ON p.id = s.product_id WHERE s.is_voided = 0"""
    params = []
    if date_from:
        query += " AND s.sale_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND s.sale_date <= ?"
        params.append(date_to)
    with db_cursor() as cur:
        rows = cur.execute(query, params).fetchall()
    total_revenue = sum(r["selling_price"] * r["quantity"] for r in rows)
    total_cogs = sum(r["purchase_price"] * r["quantity"] for r in rows)

    # Realized revenue and COGS (only paid amounts)
    # For each transaction, calculate what portion was paid
    realized_query = """
        SELECT
            t.id AS transaction_id,
            SUM(s.quantity * s.selling_price) AS total_sale_amount,
            COALESCE(SUM(sp.amount), 0) AS paid_amount,
            SUM(s.quantity * p.purchase_price) AS total_cogs
        FROM transactions t
        JOIN sales s ON s.transaction_id = t.id
        JOIN products p ON p.id = s.product_id
        LEFT JOIN sale_payments sp ON sp.transaction_id = t.id
        WHERE s.is_voided = 0
    """
    realized_params = []
    if date_from:
        realized_query += " AND s.sale_date >= ?"
        realized_params.append(date_from)
    if date_to:
        realized_query += " AND s.sale_date <= ?"
        realized_params.append(date_to)
    realized_query += " GROUP BY t.id"

    realized_revenue = 0.0
    realized_cogs = 0.0
    with db_cursor() as cur:
        for row in cur.execute(realized_query, realized_params).fetchall():
            total_sale = float(row["total_sale_amount"] or 0)
            paid = float(row["paid_amount"] or 0)
            total_cogs_for_tx = float(row["total_cogs"] or 0)
            
            if total_sale > 0:
                # Only count the COGS proportion for what was actually paid
                paid_ratio = min(paid / total_sale, 1.0)  # Cap at 1.0 for overpayments
                realized_revenue += paid
                realized_cogs += total_cogs_for_tx * paid_ratio

    expenses_total = expense_service.total_expenses(date_from, date_to)
    expenses_by_method = expense_service.expenses_by_method(date_from, date_to)
    purchases_total = purchase_service.total_purchases(date_from, date_to)
    purchases_by_method = purchase_service.purchases_by_method(date_from, date_to)
    payments = payment_totals(date_from=date_from, date_to=date_to)

    total_adj = adjustment_service.adjustment_total("total", date_from, date_to)
    expenses_adj = adjustment_service.adjustment_total("expenses", date_from, date_to)
    purchases_adj = adjustment_service.adjustment_total("purchases", date_from, date_to)
    net_profit_adj = adjustment_service.adjustment_total("net_profit", date_from, date_to)
    online_adj = adjustment_service.adjustment_total("online", date_from, date_to)

    total_sales = total_revenue + total_adj
    cash = payments["cash"] - purchases_by_method["cash"] - expenses_by_method["cash"]
    online = payments["online"] - purchases_by_method["online"] - expenses_by_method["online"] + online_adj
    expenses_total += expenses_adj
    purchases_total += purchases_adj
    writeoff_cost = writeoff_service.totals(date_from=date_from, date_to=date_to)["cost_loss"]

    # Realized Net Profit: Only from money that has actually been collected
    realized_net_profit = realized_revenue - realized_cogs - expenses_total - writeoff_cost + net_profit_adj

    # Potential Net Profit: What it would be if all outstanding debts were collected
    potential_net_profit = total_revenue - total_cogs - expenses_total - writeoff_cost + net_profit_adj

    return {
        "total": total_sales,
        "expenses": expenses_total,
        "purchases": purchases_total,
        "net_profit": realized_net_profit,  # Keep existing key name for backward compatibility
        "potential_net_profit": potential_net_profit,
        "outstanding_debt": total_revenue - realized_revenue,
        "online": online,
    }


def financial_ledger(date_from=None, date_to=None, group_by="day"):
    """Every pound in and out: Sales, Purchases, Expenses, هالك write-offs,
    and manual adjustments merged into one chronological list, plus a
    grouped day/week/year summary for the history table.

    هالك entries were previously missing here entirely - write-offs
    reduce net profit in every other total in the app (Owner Dashboard,
    Reports range summary, now today_report), but were invisible in this
    chronological ledger, so a profit dip from a write-off had no
    corresponding line item explaining it.

    BUG FIX: every entry now carries an "id" key (and the sales query
    now actually selects s.id). The delete button on the Reports page
    ledger table (reports.html) calls
    url_for('reports.delete_ledger_entry', entry_type=e.type, entry_id=e.id)
    for every row - previously none of these dicts had an "id" key at
    all, which crashed the whole page with a Jinja UndefinedError
    ('dict object' has no attribute 'id') the moment any ledger row
    existed.
    """
    from app.services import expenses as expense_service
    from app.services import purchases as purchase_service
    from app.services import adjustments as adjustment_service
    from app.services import writeoffs as writeoff_service

    entries = []

    sales_query = """SELECT s.id AS id, s.sale_date AS date, s.transaction_id, s.quantity,
                             s.selling_price,
                             COALESCE(NULLIF(TRIM(s.custom_product_name), ''), s.service_description, p.name) AS product_name
                      FROM sales s JOIN products p ON p.id = s.product_id
                      WHERE s.is_voided = 0"""
    sales_params = []
    if date_from:
        sales_query += " AND s.sale_date >= ?"
        sales_params.append(date_from)
    if date_to:
        sales_query += " AND s.sale_date <= ?"
        sales_params.append(date_to)
    with db_cursor() as cur:
        for r in cur.execute(sales_query, sales_params).fetchall():
            label = f"Invoice #{r['transaction_id']} - {r['quantity']}x {r['product_name']}" \
                if r["transaction_id"] else f"{r['quantity']}x {r['product_name']}"
            entries.append({"id": r["id"], "date": r["date"], "type": "sale", "description": label,
                             "debit": 0, "credit": r["quantity"] * r["selling_price"]})

    for p in purchase_service.list_purchases(date_from, date_to):
        entries.append({"id": p["id"], "date": p["purchase_date"], "type": "purchase",
                         "description": p["name"], "debit": p["cost"], "credit": 0})

    for e in expense_service.list_expenses(date_from, date_to):
        entries.append({"id": e["id"], "date": e["expense_date"], "type": "expense",
                         "description": e["description"], "debit": e["amount"], "credit": 0})

    for w in writeoff_service.list_all(date_from, date_to):
        note = f" - {w['note']}" if w.get("note") else ""
        entries.append({
            "id": w["id"], "date": w["writeoff_date"], "type": "writeoff",
            "description": f"هالك - {w['quantity']}x {w['product_name']}{note}",
            "debit": w["cost_loss"], "credit": 0,
        })

    for a in adjustment_service.list_adjustments(date_from, date_to):
        note = f" - {a['note']}" if a["note"] else ""
        entries.append({
            "id": a["id"], "date": a["adjustment_date"], "type": "adjustment",
            "description": f"Manual adjustment ({a['target']}){note}",
            "debit": abs(a["amount"]) if a["amount"] < 0 else 0,
            "credit": a["amount"] if a["amount"] > 0 else 0,
        })

    entries.sort(key=lambda e: e["date"], reverse=True)
    return {"entries": entries, "grouped": _group_ledger(entries, group_by)}


def _period_key(date_str, group_by):
    d = date_str[:10]
    if group_by == "year":
        return d[:4]
    if group_by == "week":
        from datetime import date as _date
        y, m, day = (int(x) for x in d.split("-"))
        iso = _date(y, m, day).isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    return d


def _group_ledger(entries, group_by):
    buckets = {}
    for e in entries:
        key = _period_key(e["date"], group_by)
        b = buckets.setdefault(key, {"period": key, "incoming": 0.0, "outgoing": 0.0})
        b["incoming"] += e["credit"]
        b["outgoing"] += e["debit"]
    rows = list(buckets.values())
    rows.sort(key=lambda r: r["period"], reverse=True)
    for r in rows:
        r["net"] = r["incoming"] - r["outgoing"]
    return rows


def inventory_value():
    with db_cursor() as cur:
        row = cur.execute(
            "SELECT COALESCE(SUM(quantity * purchase_price), 0) AS value, "
            "COALESCE(SUM(quantity), 0) AS units "
            "FROM products WHERE is_active = 1"
        ).fetchone()
    return {"inventory_value": row["value"], "units_in_stock": row["units"]}


def low_stock(threshold=None):
    from app.services import settings as settings_service
    threshold = threshold if threshold is not None else settings_service.low_stock_threshold()
    with db_cursor() as cur:
        rows = cur.execute(
            """SELECT p.*, c.name AS category_name FROM products p
               JOIN categories c ON c.id = p.category_id
               WHERE p.is_active = 1 AND p.quantity > 0 AND p.quantity <= ?
               ORDER BY p.quantity, p.name""",
            (threshold,),
        ).fetchall()
    return [dict(r) for r in rows]


def sales_history(date_from=None, date_to=None):
    from app.services.sales import list_sales
    return list_sales(date_from=date_from, date_to=date_to)
