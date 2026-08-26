"""
Owner analytics dashboard data — Chart.js-ready JSON.

Standard accounting net profit (extended for write-offs):
  Net Profit = Revenue - COGS - Expenses - WriteoffCostLoss

  Revenue  = non-voided sales (selling_price × qty)
  COGS     = cost of goods sold only (qty sold × product.purchase_price)
  Expenses = operating expenses table only (rent, salaries, utilities, …)
  Writeoff = stock_writeoffs.cost_loss (qty × purchase_price at write-off)

Purchases are NOT part of Expenses / Net Profit — Chart 3 only.

راكد (stagnant, >60 days unsold) and هالك (write-offs) are both forms
of tied-up / lost liquidity, so they're shown together as ONE combined
chart (single stacked bar) instead of two separate charts.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from collections import OrderedDict

from app.db import db_cursor
from app.services.products import is_stagnant_product

TIMEFRAMES = {
    "weekly": {"days": 7, "bucket": "day", "label_ar": "أسبوعي", "label_en": "Weekly"},
    "monthly": {"days": 30, "bucket": "day", "label_ar": "شهري", "label_en": "Monthly"},
    "6months": {"days": 183, "bucket": "month", "label_ar": "٦ أشهر", "label_en": "6 Months"},
    "yearly": {"days": 365, "bucket": "month", "label_ar": "سنوي", "label_en": "Yearly"},
}
DEFAULT_TIMEFRAME = "6months"
STAGNANT_DAYS = 60


def _parse_timeframe(timeframe: str) -> dict:
    key = (timeframe or DEFAULT_TIMEFRAME).strip().lower().replace(" ", "").replace("-", "")
    aliases = {"week": "weekly", "month": "monthly", "6m": "6months", "sixmonths": "6months", "year": "yearly"}
    key = aliases.get(key, key)
    return TIMEFRAMES.get(key, TIMEFRAMES[DEFAULT_TIMEFRAME]) | {"key": key if key in TIMEFRAMES else DEFAULT_TIMEFRAME}


def _range_for(timeframe: str, reset_at: str | None = None):
    """start/end for the requested rolling window (e.g. "last 30 days").

    reset_at: when إعادة ضبط التقارير has been used, this is
    settings.reports_reset_at. If it falls *after* the window's natural
    start, the window's start is pulled forward to it - so a report
    reset earlier today or last week actually clips how far back the
    analytics KPIs/charts look, instead of silently ignoring the reset
    the way the rest of the Reports page never did (the reset bug).
    A reset_at that's older than the window's natural start changes
    nothing, since the window was already narrower than "everything
    since the reset" in that case.
    """
    meta = _parse_timeframe(timeframe)
    end = date.today()
    start = end - timedelta(days=meta["days"] - 1)
    if reset_at:
        try:
            reset_date = date.fromisoformat((reset_at or "")[:10])
            if reset_date > start:
                start = reset_date
        except ValueError:
            pass
    return start.isoformat(), (end.isoformat() + "T23:59:59"), meta


def _bucket_key(date_str: str, bucket: str) -> str:
    d = (date_str or "")[:10]
    if len(d) < 7:
        return d or "unknown"
    if bucket == "month":
        return d[:7]  # YYYY-MM
    return d  # YYYY-MM-DD


def _empty_buckets(start_iso: str, end_iso: str, bucket: str) -> OrderedDict:
    """Pre-fill every day/month in range so charts have continuous axes."""
    start = date.fromisoformat(start_iso[:10])
    end = date.fromisoformat(end_iso[:10])
    buckets = OrderedDict()
    if bucket == "month":
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            buckets[f"{y:04d}-{m:02d}"] = None
            m += 1
            if m > 12:
                m = 1
                y += 1
    else:
        cur = start
        while cur <= end:
            buckets[cur.isoformat()] = None
            cur += timedelta(days=1)
    for k in buckets:
        buckets[k] = 0.0
    return buckets


def _label_for_bucket(key: str, bucket: str) -> str:
    if bucket == "month" and len(key) >= 7:
        try:
            dt = datetime.strptime(key[:7], "%Y-%m")
            return dt.strftime("%b %Y")
        except ValueError:
            return key
    if len(key) >= 10:
        try:
            dt = datetime.strptime(key[:10], "%Y-%m-%d")
            return dt.strftime("%d %b")
        except ValueError:
            return key
    return key


def _sum_sales_by_bucket(date_from, date_to, bucket):
    """Returns {bucket: {revenue, cogs, units}}."""
    data = {}
    # Delivery-order sales whose money hasn't been financially recognized
    # yet (orders.financially_completed_at IS NULL) are excluded here -
    # creating a delivery order must never inflate revenue before the
    # order reaches وصل and its payment is confirmed. See
    # app/services/orders.py pending_order_sql_exclusion().
    from app.services import orders as order_service
    query = f"""
        SELECT s.sale_date, s.quantity, s.selling_price, p.purchase_price
        FROM sales s
        JOIN products p ON p.id = s.product_id
        WHERE s.is_voided = 0 AND s.sale_date >= ? AND s.sale_date <= ?
          AND {order_service.pending_order_sql_exclusion("s")}
    """
    with db_cursor() as cur:
        rows = cur.execute(query, (date_from, date_to)).fetchall()
    for r in rows:
        key = _bucket_key(r["sale_date"], bucket)
        cell = data.setdefault(key, {"revenue": 0.0, "cogs": 0.0, "units": 0})
        qty = r["quantity"] or 0
        cell["revenue"] += (r["selling_price"] or 0) * qty
        cell["cogs"] += (r["purchase_price"] or 0) * qty
        cell["units"] += qty
    return data


def _sum_table_by_bucket(table, date_col, amount_col, date_from, date_to, bucket):
    data = {}
    query = f"SELECT {date_col} AS d, {amount_col} AS amount FROM {table} WHERE {date_col} >= ? AND {date_col} <= ?"
    with db_cursor() as cur:
        rows = cur.execute(query, (date_from, date_to)).fetchall()
    for r in rows:
        key = _bucket_key(r["d"], bucket)
        data[key] = data.get(key, 0.0) + float(r["amount"] or 0)
    return data


def profit_revenue_series(date_from, date_to, bucket):
    """Revenue vs Net Profit over time.

    Net_t = Revenue_t - COGS_t - Expenses_t - SpoilageInstallment_t

    SpoilageInstallment_t is NOT the full cost of write-offs that happened
    in bucket t - it's each write-off's 1/12th monthly installment for
    every write-off whose 12-month amortization window covers t (custom
    client-requested spread; see
    docs/spoilage_management_requirements.md).
    """
    from app.services import writeoffs as writeoff_service

    sales = _sum_sales_by_bucket(date_from, date_to, bucket)
    expenses = _sum_table_by_bucket("expenses", "expense_date", "amount", date_from, date_to, bucket)
    writeoff_costs = writeoff_service.amortization_by_bucket(date_from, date_to, bucket)
    buckets = _empty_buckets(date_from, date_to, bucket)

    labels, revenue, net_profit = [], [], []
    for key in buckets:
        cell = sales.get(key, {})
        rev = cell.get("revenue", 0.0)
        cogs = cell.get("cogs", 0.0)
        exp = expenses.get(key, 0.0)
        wo = writeoff_costs.get(key, 0.0)
        profit = rev - cogs - exp - wo
        labels.append(_label_for_bucket(key, bucket))
        revenue.append(round(rev, 2))
        net_profit.append(round(profit, 2))

    return {
        "labels": labels,
        "datasets": [
            {
                "label": "الإيرادات / Revenue",
                "data": revenue,
                "borderColor": "#3B82F6",
                "backgroundColor": "rgba(59, 130, 246, 0.18)",
                "fill": True,
                "tension": 0.35,
            },
            {
                "label": "صافي الربح / Net Profit",
                "data": net_profit,
                "borderColor": "#34D399",
                "backgroundColor": "rgba(52, 211, 153, 0.12)",
                "fill": True,
                "tension": 0.35,
            },
        ],
    }


def purchases_vs_expected(date_from, date_to, bucket):
    """Cost/value of current inventory, over the selected period:
      - Stock Cost: cumulative running total (purchase_price × qty) of
        all currently active products, as of each bucket's end date -
        i.e. "cost basis of everything in the store" building up over
        time, not just what was added inside the selected window.
      - Expected Returns: a SINGLE flat figure - total anticipated
        sell-through value (selling_price × qty) of EVERY currently
        active product, right now - repeated identically across every
        bucket. Intentionally NOT time-windowed and NOT affected by
        which timeframe tab is selected: it always answers "what is the
        whole inventory worth if sold today", a snapshot of current
        reality rather than a trend.
      - المشتريات المسجلة من المخزون: cash recorded in the purchases
        table per bucket, for the selected period.
    """
    stock_cost = _empty_buckets(date_from, date_to, bucket)
    recorded = _sum_table_by_bucket("purchases", "purchase_date", "cost", date_from, date_to, bucket)

    with db_cursor() as cur:
        rows = cur.execute(
            """
            SELECT date_added, quantity, purchase_price, selling_price
            FROM products
            WHERE COALESCE(is_active, 1) = 1
              AND COALESCE(source, '') <> 'service_placeholder'
            ORDER BY date_added
            """,
        ).fetchall()

    # Expected Returns: one flat total across the ENTIRE active
    # inventory, unrelated to any bucket or date range.
    total_expected_now = sum((r["selling_price"] or 0) * (r["quantity"] or 0) for r in rows)

    # Stock Cost: running cumulative total as of each bucket's end date.
    running_cost = 0.0
    row_idx = 0
    n_rows = len(rows)
    bucket_keys = list(stock_cost.keys())  # OrderedDict, ascending chronological order
    for key in bucket_keys:
        # Month buckets are "YYYY-MM" - "-31" is always >= any real day in
        # that month, giving a safe inclusive upper bound via plain string
        # comparison against date_added ("YYYY-MM-DD"), same as day buckets
        # comparing directly.
        bucket_end = key if bucket != "month" else f"{key}-31"
        while row_idx < n_rows:
            added = (rows[row_idx]["date_added"] or "")[:10]
            if added and added > bucket_end:
                break
            qty = rows[row_idx]["quantity"] or 0
            running_cost += (rows[row_idx]["purchase_price"] or 0) * qty
            row_idx += 1
        stock_cost[key] = round(running_cost, 2)

    # Safety net: fold anything still unconsumed into the last bucket
    # instead of silently dropping it (e.g. a product's date_added newer
    # than the range's own end date, from a clock/timezone edge case).
    if row_idx < n_rows and bucket_keys:
        for i in range(row_idx, n_rows):
            qty = rows[i]["quantity"] or 0
            running_cost += (rows[i]["purchase_price"] or 0) * qty
        stock_cost[bucket_keys[-1]] = round(running_cost, 2)

    labels = [_label_for_bucket(k, bucket) for k in stock_cost]
    return {
        "labels": labels,
        "datasets": [
            {
                "label": "تكلفة المخزون / Stock Cost",
                "data": list(stock_cost.values()),
                "backgroundColor": "rgba(248, 113, 113, 0.75)",
                "borderRadius": 6,
            },
            {
                "label": "القيمة المتوقعة / Expected Returns",
                "data": [round(total_expected_now, 2)] * len(bucket_keys),
                "backgroundColor": "rgba(52, 211, 153, 0.75)",
                "borderRadius": 6,
            },
            {
                "label": "المشتريات المسجلة من المخزون / Recorded Purchases",
                "data": [round(recorded.get(k, 0.0), 2) for k in bucket_keys],
                "backgroundColor": "rgba(96, 165, 250, 0.65)",
                "borderRadius": 6,
            },
        ],
    }


def stagnant_and_damaged(date_from=None, date_to=None):
    """راكد (stagnant shelf liquidity, >60 days) + هالك (explicit
    stock_writeoffs ledger) — merged into ONE combined "at-risk
    liquidity" view instead of two separate charts.
    """
    from app.services import writeoffs as writeoff_service

    cutoff = (date.today() - timedelta(days=STAGNANT_DAYS)).isoformat()

    with db_cursor() as cur:
        stagnant_rows = cur.execute(
            """
            SELECT p.id, p.name, p.quantity, p.purchase_price, p.date_added,
                   (SELECT MAX(s.sale_date) FROM sales s
                    WHERE s.product_id = p.id AND s.is_voided = 0) AS last_sale
            FROM products p
            WHERE p.is_active = 1
              AND p.quantity > 0
              AND COALESCE(p.source, '') <> 'service_placeholder'
            ORDER BY (p.quantity * p.purchase_price) DESC
            """,
        ).fetchall()

    stagnant_items = []
    stagnant_value = 0.0
    for row in stagnant_rows:
        r = dict(row)
        if not is_stagnant_product(r, cutoff=cutoff):
            continue
        val = (r["quantity"] or 0) * (r["purchase_price"] or 0)
        stagnant_value += val
        stagnant_items.append({
            "id": r["id"],
            "name": r["name"],
            "quantity": r["quantity"],
            "value": round(val, 2),
            "date_added": (r["date_added"] or "")[:10],
            "last_sale": (r["last_sale"] or "")[:10] or None,
        })

    wo = writeoff_service.totals(date_from=date_from, date_to=date_to)
    damaged_value = wo["cost_loss"]
    damaged_items = []
    for r in writeoff_service.recent_items(limit=25, date_from=date_from, date_to=date_to):
        damaged_items.append({
            "id": r["product_id"],
            "name": r["product_name"],
            "quantity": r["quantity"],
            "value": round(float(r["cost_loss"] or 0), 2),
            "revenue_loss": round(float(r["revenue_loss"] or 0), 2),
            "writeoff_date": (r["writeoff_date"] or "")[:10],
            "purchase_price": r["purchase_price"],
            "selling_price": r["selling_price"],
        })

    total_at_risk = stagnant_value + damaged_value

    # Single stacked bar, one category, two segments: راكد + هالك.
    combined_chart = {
        "labels": ["السيولة المعرضة للخطر"],
        "datasets": [
            {
                "label": "راكد >60 يوم",
                "data": [round(stagnant_value, 2)],
                "backgroundColor": "rgba(251, 191, 36, 0.85)",
                "borderRadius": 8,
                "stack": "risk",
            },
            {
                "label": "هالك",
                "data": [round(damaged_value, 2)],
                "backgroundColor": "rgba(248, 113, 113, 0.85)",
                "borderRadius": 8,
                "stack": "risk",
            },
        ],
    }

    return {
        "stagnant_value": round(stagnant_value, 2),
        "damaged_value": round(damaged_value, 2),
        "total_at_risk_value": round(total_at_risk, 2),
        "damaged_revenue_loss": round(wo["revenue_loss"], 2),
        "stagnant_count": len(stagnant_items),
        "damaged_count": wo["count"],
        "stagnant_items": stagnant_items[:25],
        "damaged_items": damaged_items,
        "combined_chart": combined_chart,
    }


def kpis(date_from, date_to):
    """KPI strip — Net Profit calculation with customer debt handling.
    
    Accounting Fix: Now calculates REALIZED net profit (based on payments collected)
    rather than including unpaid customer debts.
    
    Realized Net Profit = Collected Revenue - Realized COGS - Expenses - Writeoff cost
    Potential Net Profit = Total Revenue - Total COGS - Expenses - Writeoff cost
    """
    from app.services import writeoffs as writeoff_service

    # Total sales data
    sales = _sum_sales_by_bucket(date_from, date_to, "day")
    total_revenue = sum(v["revenue"] for v in sales.values())
    total_cogs = sum(v["cogs"] for v in sales.values())
    units = sum(v["units"] for v in sales.values())

    # Calculate realized revenue and COGS based on actual payments
    realized_revenue = 0.0
    realized_cogs = 0.0
    
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
        WHERE s.is_voided = 0 AND s.sale_date >= ? AND s.sale_date <= ?
        GROUP BY t.id
    """
    
    with db_cursor() as cur:
        for row in cur.execute(realized_query, (date_from, date_to)).fetchall():
            total_sale = float(row["total_sale_amount"] or 0)
            paid = float(row["paid_amount"] or 0)
            total_cogs_for_tx = float(row["total_cogs"] or 0)
            
            if total_sale > 0:
                # Only count the COGS proportion for what was actually paid
                paid_ratio = min(paid / total_sale, 1.0)  # Cap at 1.0 for overpayments
                realized_revenue += paid
                realized_cogs += total_cogs_for_tx * paid_ratio

    with db_cursor() as cur:
        expenses = cur.execute(
            "SELECT COALESCE(SUM(amount), 0) AS t FROM expenses WHERE expense_date >= ? AND expense_date <= ?",
            (date_from, date_to),
        ).fetchone()["t"]
        purchases = cur.execute(
            "SELECT COALESCE(SUM(cost), 0) AS t FROM purchases WHERE purchase_date >= ? AND purchase_date <= ?",
            (date_from, date_to),
        ).fetchone()["t"]
        inventory = cur.execute(
            """
            SELECT COALESCE(SUM(quantity * purchase_price), 0) AS value,
                   COALESCE(SUM(quantity), 0) AS units
            FROM products WHERE is_active = 1 AND COALESCE(source, '') <> 'service_placeholder'
            """
        ).fetchone()

    expenses = float(expenses or 0)
    purchases = float(purchases or 0)
    # Net Profit uses the amortized (12-month spread) spoilage installment
    # due in [date_from, date_to], NOT the full write-off cost - custom
    # client-requested override, see
    # docs/spoilage_management_requirements.md. The full, un-amortized
    # cost still drives the "at-risk liquidity" KPI below (stock_and_damaged/
    # stagnant_and_damaged), which answers a different question (how much
    # capital is tied up in damaged stock right now).
    writeoff_cost = writeoff_service.amortized_total(date_from=date_from, date_to=date_to)
    # Only revenue_loss (never amortized - it's informational, not a P&L
    # deduction) is still read from the full, un-amortized totals().
    wo = writeoff_service.totals(date_from=date_from, date_to=date_to)

    # Realized net profit: from money that has actually been collected
    realized_net_profit = realized_revenue - realized_cogs - expenses - writeoff_cost
    
    # Potential net profit: from all sales including unpaid debt
    potential_net_profit = total_revenue - total_cogs - expenses - writeoff_cost
    
    # Outstanding debt
    outstanding_debt = total_revenue - realized_revenue
    
    stock = stagnant_and_damaged(date_from=date_from, date_to=date_to)

    return {
        "revenue": round(total_revenue, 2),
        "realized_revenue": round(realized_revenue, 2),
        "cogs": round(total_cogs, 2),
        "realized_cogs": round(realized_cogs, 2),
        "gross_profit": round(total_revenue - total_cogs, 2),
        "realized_gross_profit": round(realized_revenue - realized_cogs, 2),
        "net_profit": round(realized_net_profit, 2),  # Realized profit (primary value)
        "potential_net_profit": round(potential_net_profit, 2),
        "outstanding_debt": round(outstanding_debt, 2),
        "expenses": round(expenses, 2),
        "purchases": round(purchases, 2),
        "writeoff_cost": round(writeoff_cost, 2),
        "writeoff_revenue_loss": round(wo["revenue_loss"], 2),
        "units_sold": units,
        "inventory_value": round(inventory["value"], 2),
        "units_in_stock": inventory["units"],
        "stagnant_value": stock["stagnant_value"],
        "damaged_value": stock["damaged_value"],
        "total_at_risk_value": stock["total_at_risk_value"],
    }


def build_dashboard_payload(timeframe: str = DEFAULT_TIMEFRAME, reset_at: str | None = None) -> dict:
    # reset_at is accepted for backward compatibility with existing
    # callers but intentionally unused here now: clipping this section's
    # date range to "since the last reset" meant every timeframe tab
    # (weekly/monthly/6months/yearly) could collapse to a single
    # near-empty bucket whenever a reset had been used recently, breaking
    # revenue/profit/purchases-vs-expected regardless of which tab was
    # selected. Analytics here always reflects the timeframe's own full
    # natural window - إعادة ضبط التقارير still correctly zeroes the
    # separate cash-drawer totals on the main Reports page (reports.py),
    # which this doesn't touch.
    date_from, date_to, meta = _range_for(timeframe, reset_at=None)
    bucket = meta["bucket"]

    stock = stagnant_and_damaged(date_from=date_from, date_to=date_to)

    return {
        "timeframe": meta["key"],
        "timeframe_label": meta["label_ar"],
        "date_from": date_from,
        "date_to": date_to[:10],
        "bucket": bucket,
        "kpis": kpis(date_from, date_to),
        "charts": {
            "profit_revenue": profit_revenue_series(date_from, date_to, bucket),
            "purchases_expected": purchases_vs_expected(date_from, date_to, bucket),
            "stock_at_risk": stock["combined_chart"],
        },
        "stock_details": {
            "stagnant_items": stock["stagnant_items"],
            "damaged_items": stock["damaged_items"],
            "stagnant_count": stock["stagnant_count"],
            "damaged_count": stock["damaged_count"],
            "damaged_revenue_loss": stock.get("damaged_revenue_loss", 0),
        },
    }
