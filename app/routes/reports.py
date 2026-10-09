import io
from datetime import datetime
from flask import (
    Blueprint, render_template, request, send_file, flash, redirect,
    url_for, session, jsonify, abort,
)
from app.services import reports as report_service
from app.services import receipts as receipt_service
from app.services import expenses as expense_service
from app.services import purchases as purchase_service
from app.services import adjustments as adjustment_service
from app.services import owner_dashboard as owner_service
from app.services import sales as sales_service
from app.services import writeoffs as writeoff_service

bp = Blueprint("reports", __name__, url_prefix="/reports")

# Maps a financial_ledger() entry "type" to the service function that
# deletes that kind of row. Every entry produced by
# report_service.financial_ledger() now carries an "id" specifically so
# this can route to the right place - see app/services/reports.py.
_LEDGER_DELETERS = {
    "sale": lambda entry_id: sales_service.delete_sale_line(entry_id),
    "purchase": purchase_service.delete_purchase,
    "expense": expense_service.delete_expense,
    "writeoff": writeoff_service.delete_writeoff,
    "adjustment": adjustment_service.delete_adjustment,
}


def _build_report(date_from, date_to):
    """Kept for the existing Excel export route - unrelated to the
    stat cards on the page itself."""
    payments = report_service.payment_totals(date_from, date_to)
    expenses_total = expense_service.total_expenses(date_from, date_to)
    purchases_by_method = purchase_service.purchases_by_method(date_from, date_to)
    cash = payments["cash"] - purchases_by_method["cash"]
    online = payments["online"] - purchases_by_method["online"]
    return {
        "cash": cash,
        "online": online,
        "total": cash + online - expenses_total,
        "expenses": expenses_total,
    }


@bp.route("/")
def index():
    date_from = request.args.get("from") or None
    date_to = request.args.get("to") or None

    # الدرج: the explicit "من" filter if the user set one, all-time
    # otherwise. (إعادة ضبط التقارير removed entirely - no more
    # reset-point concept here.)
    drawer_report = report_service.today_report(
        date_from=date_from,
        all_time_if_none=(date_from is None),
    )

    today_only_report = report_service.today_report()
    today_key = datetime.now().date().isoformat()

    range_summary = report_service.date_range_summary(date_from, date_to)
    ledger_entries = report_service.financial_ledger(date_from, date_to)["entries"]

    return render_template(
        "reports.html",
        drawer=drawer_report["drawer"],
        today_total=today_only_report["today_total"],
        today_report_url=url_for("reports.today"),
        drawer_report_url=url_for("reports.drawer"),
        vodafone_cash_report_url=url_for("reports.vodafone_cash"),
        instapay_report_url=url_for("reports.instapay"),
        range_summary=range_summary,
        ledger_entries=ledger_entries,
        adjustment_targets=(
            [target for target in adjustment_service.TARGETS if target[0] not in {"online", "today"}]
            if session.get("role") == "admin"
            else [("drawer", "الدرج"), ("instapay", "InstaPay"), ("vodafone_cash", "Vodafone Cash")]
        ),
        date_from=date_from,
        date_to=date_to,
        default_timeframe=owner_service.DEFAULT_TIMEFRAME,
    )


@bp.route("/adjust", methods=["POST"])
def adjust():
    target = request.form.get("target")
    if session.get("role") != "admin" and target not in {"drawer", "instapay", "vodafone_cash"}:
        abort(403)
    kind = request.form.get("kind", "add")
    amount = request.form.get("amount", type=float)
    note = request.form.get("note", "")
    try:
        signed_amount = amount if kind == "add" else -(amount or 0)
        adjustment_service.add_adjustment(target, signed_amount, note)
        flash("تم تسجيل التعديل بنجاح.", "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("reports.index", **request.args))


@bp.route("/ledger/<entry_type>/<int:entry_id>/delete", methods=["POST"])
def delete_ledger_entry(entry_type, entry_id):
    deleter = _LEDGER_DELETERS.get(entry_type)
    if deleter is None:
        abort(404)
    try:
        deleter(entry_id)
        flash("تم الحذف بنجاح.", "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("reports.index", **request.args))


@bp.route("/today")
def today():
    """معاملات اليوم - today only. Also takes an optional ?q= search
    (text match on each entry's description), same convention as every
    other search box in this app (orders.html, sales_history.html)."""
    today_key = datetime.now().date().isoformat()
    report = report_service.today_report()
    query = request.args.get("q", "").strip() or None
    transactions = report_service.financial_ledger(
        f"{today_key} 00:00:00", f"{today_key}T23:59:59"
    )["entries"]
    if query:
        needle = query.lower()
        transactions = [t for t in transactions if needle in (t.get("description") or "").lower()]
    return render_template(
        "today_report.html",
        report=report,
        transactions=transactions,
        today_key=today_key,
        search=query,
    )


@bp.route("/method/drawer")
def drawer():
    """كل معاملات الدرج - every cash-bucket interaction since this
    feature started tracking (no date-scoping/reset concept - reports
    reset was removed). Same composition as the الدرج figure itself:
    cash payments in, cash purchases/expenses out, drawer adjustments -
    see report_service.method_ledger()."""
    query = request.args.get("q", "").strip() or None
    date_from = request.args.get("from") or None
    date_to = request.args.get("to") or None
    entries = report_service.method_ledger("cash", query=query, date_from=date_from, date_to=date_to)
    return render_template(
        "method_report.html",
        title="الدرج",
        entries=entries,
        search=query,
        date_from=date_from,
        date_to=date_to,
        page_endpoint="reports.drawer",
    )


@bp.route("/method/vodafone-cash")
def vodafone_cash():
    query = request.args.get("q", "").strip() or None
    date_from = request.args.get("from") or None
    date_to = request.args.get("to") or None
    entries = report_service.method_ledger("vodafone_cash", query=query, date_from=date_from, date_to=date_to)
    return render_template(
        "method_report.html",
        title="Vodafone Cash",
        entries=entries,
        search=query,
        date_from=date_from,
        date_to=date_to,
        page_endpoint="reports.vodafone_cash",
    )


@bp.route("/method/instapay")
def instapay():
    query = request.args.get("q", "").strip() or None
    date_from = request.args.get("from") or None
    date_to = request.args.get("to") or None
    entries = report_service.method_ledger("instapay", query=query, date_from=date_from, date_to=date_to)
    return render_template(
        "method_report.html",
        title="InstaPay",
        entries=entries,
        search=query,
        date_from=date_from,
        date_to=date_to,
        page_endpoint="reports.instapay",
    )


@bp.route("/today/export.pdf")
def today_report_export_pdf():
    pdf_bytes = receipt_service.today_report_pdf_bytes(report_service.today_report())
    return send_file(io.BytesIO(pdf_bytes), as_attachment=True,
                      download_name="alqemma_today_report.pdf", mimetype="application/pdf")


@bp.route("/export/excel")
def export_excel():
    date_from = request.args.get("from") or None
    date_to = request.args.get("to") or None
    buf = receipt_service.report_excel_bytes(_build_report(date_from, date_to))
    return send_file(buf, as_attachment=True, download_name="alqemma_report.xlsx",
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/export/pdf")
def export_pdf():
    date_from = request.args.get("from") or None
    date_to = request.args.get("to") or None
    pdf_bytes = receipt_service.report_pdf_bytes(_build_report(date_from, date_to), date_from, date_to)
    return send_file(io.BytesIO(pdf_bytes), as_attachment=True,
                      download_name="alqemma_report.pdf", mimetype="application/pdf")


@bp.route("/api/analytics")
def analytics_api():
    if session.get("role") != "admin":
        return jsonify({"error": "forbidden"}), 403

    timeframe = request.args.get("timeframe") or owner_service.DEFAULT_TIMEFRAME
    payload = owner_service.build_dashboard_payload(timeframe)
    return jsonify(payload)


@bp.route("/analytics/reset/<kpi>", methods=["POST"])
def reset_analytics_kpi(kpi):
    """Reset one analytics KPI and its graph without deleting source records."""
    if session.get("role") != "admin":
        abort(403)
    if kpi not in {"net_profit", "purchases", "at_risk"}:
        abort(404)
    owner_service.set_reset_at(kpi)
    flash("تمت إعادة الضبط بنجاح.", "success")
    return redirect(url_for("reports.index"))