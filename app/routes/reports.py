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
from app.services import settings as settings_service
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


def _effective_date_from(date_from):
    """If the user picked an explicit start date, that always wins. Otherwise,
    if "Reset Reports" has been used, totals start from that point instead
    of all-time."""
    return date_from or settings_service.get("reports_reset_at")


def _build_report(date_from, date_to):
    """Kept for the existing Excel export route - unrelated to the
    stat cards on the page itself."""
    effective_from = _effective_date_from(date_from)
    payments = report_service.payment_totals(effective_from, date_to)
    expenses_total = expense_service.total_expenses(effective_from, date_to)
    purchases_by_method = purchase_service.purchases_by_method(effective_from, date_to)
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
    effective_from = _effective_date_from(date_from)
    reset_at = settings_service.get("reports_reset_at")

    today_date_from = reset_at if date_from is None else None

    # Drawer card: intentionally scoped to "since the last manual reset"
    # (or all-time if reports have never been reset) - that's the whole
    # point of the الدرج card and the "إعادة ضبط التقارير" button.
    drawer_report = report_service.today_report(
        date_from=today_date_from,
        all_time_if_none=(today_date_from is None),
    )

    # BUG FIX (اليوم card never actually reset daily): this card was
    # previously read from the same drawer_report call above, which uses
    # reset_at (or all-time) as its date_from - never "today". So اليوم
    # was really showing "everything since the last manual reset" (or
    # all-time if reports were never reset), and only ever looked like a
    # daily reset by coincidence if someone happened to click "إعادة ضبط
    # التقارير" at midnight. A calendar-day reset must not depend on that
    # manual action at all. Calling today_report() with no arguments
    # takes its `else` branch (see app/services/reports.py), which always
    # scopes to date.today() - re-evaluated fresh on every request, so it
    # naturally rolls over at midnight even if the app has been running
    # for days without a restart.
    today_only_report = report_service.today_report()

    range_summary = report_service.date_range_summary(effective_from, date_to)
    ledger_entries = report_service.financial_ledger(effective_from, date_to)["entries"]

    return render_template(
        "reports.html",
        drawer=drawer_report["drawer"],
        today_total=today_only_report["today_total"],
        range_summary=range_summary,
        ledger_entries=ledger_entries,
        adjustment_targets=adjustment_service.TARGETS,
        date_from=date_from,
        date_to=date_to,
        reset_at=reset_at,
        # Only used by the admin-only analytics section further down the
        # page; harmless to pass regardless of role.
        default_timeframe=owner_service.DEFAULT_TIMEFRAME,
    )


@bp.route("/adjust", methods=["POST"])
def adjust():
    target = request.form.get("target")
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


@bp.route("/clear", methods=["POST"])
def clear():
    settings_service.set(
        "reports_reset_at",
        datetime.now().isoformat(sep=" ", timespec="seconds"),
    )
    flash("Reports reset — totals now start from zero. Nothing was deleted.", "success")
    return redirect(url_for("reports.index"))


@bp.route("/ledger/<entry_type>/<int:entry_id>/delete", methods=["POST"])
def delete_ledger_entry(entry_type, entry_id):
    """حذف on a Reports-page ledger row. Routes to whichever service
    already owns that kind of record - reuses delete_purchase/
    delete_expense (pre-existing) and the new delete_sale_line/
    delete_writeoff/delete_adjustment, so there's exactly one delete
    implementation per record type shared with every other page that
    can delete the same kind of thing (e.g. a sale line deleted from
    here is identical to deleting it from Sale Detail)."""
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
    return render_template("today_report.html", report=report_service.today_report())


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
    """Chart.js-ready JSON for the admin-only analytics section embedded in
    the Reports page. ?timeframe=weekly|monthly|6months|yearly
    Moved in from the old standalone /owner page — same owner_dashboard
    service call, same admin gate, nothing recalculated differently.

    BUG FIX: now passes reports_reset_at through to
    owner_service.build_dashboard_payload(), which clips the rolling
    window's start date at the reset point when one is set. Previously
    this section ignored إعادة ضبط التقارير completely - it's why KPIs
    and charts kept showing real historical numbers no matter how many
    times Reset was pressed.
    """
    if session.get("role") != "admin":
        return jsonify({"error": "forbidden"}), 403

    timeframe = request.args.get("timeframe") or owner_service.DEFAULT_TIMEFRAME
    reset_at = settings_service.get("reports_reset_at")
    payload = owner_service.build_dashboard_payload(timeframe, reset_at=reset_at)
    return jsonify(payload)
