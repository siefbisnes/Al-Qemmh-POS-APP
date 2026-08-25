from flask import Blueprint, render_template, request, redirect, url_for, flash

from app.routes.notifications import notify_event
from app.services import expenses as expense_service
from app.services import purchases as purchase_service
from app.services.sales import PAYMENT_METHODS

bp = Blueprint("expenses", __name__, url_prefix="/expenses")


def _combine_cost_records(purchases, expenses):
    records = []
    for p in purchases:
        records.append({
            "id": p["id"],
            "type": "purchase",
            "label": "مشتريات",
            "date": p["purchase_date"],
            "description": p["name"],
            "amount": p["cost"],
            "payment_method": p["payment_method"],
        })
    for e in expenses:
        records.append({
            "id": e["id"],
            "type": "expense",
            "label": "مصروفات",
            "date": e["expense_date"],
            "description": e["description"],
            "amount": e["amount"],
            "payment_method": e.get("payment_method", "cash"),
        })
    records.sort(key=lambda r: r["date"] or "", reverse=True)
    return records


@bp.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        entry_type = request.form.get("entry_type", "expense")
        description = request.form.get("description", "").strip()
        amount_value = request.form.get("amount", "")
        date_value = request.form.get("date") or None

        if entry_type == "purchase":
            try:
                amount = float(amount_value)
                payment_method = request.form.get("payment_method", "cash")
                purchase_service.create_purchase(
                    name=description,
                    cost=amount,
                    payment_method=payment_method,
                    purchase_date=date_value,
                )
                title = "Expense Registered"
                method_text = "الدرج" if payment_method == "cash" else "أونلاين"
                message = f"تم خصم {amount:.2f} ج.م من {method_text} لحساب {description}."
                notify_event("expense", title, message, url=url_for("expenses.index"))
                flash("Purchase recorded.", "success")
            except ValueError as e:
                flash(str(e), "error")
        else:
            try:
                amount = float(amount_value)
                if not description or amount <= 0:
                    raise ValueError("Description and amount are required.")
                payment_method = request.form.get("payment_method", "cash")
                expense_service.add_expense(
                    description,
                    amount,
                    date_value,
                    payment_method=payment_method,
                )
                title = "Expense Registered"
                method_text = "الدرج" if payment_method == "cash" else "أونلاين"
                message = f"تم خصم {amount:.2f} ج.م من {method_text} لحساب {description}."
                notify_event("expense", title, message, url=url_for("expenses.index"))
                flash("Expense recorded.", "success")
            except ValueError as e:
                flash(str(e), "error")

        return redirect(url_for("expenses.index"))

    date_from = request.args.get("from") or None
    date_to = request.args.get("to") or None
    purchases = purchase_service.list_purchases(date_from, date_to)
    expenses = expense_service.list_expenses(date_from, date_to)
    records = _combine_cost_records(purchases, expenses)
    purchase_online = purchase_service.purchases_by_method(date_from, date_to)["online"]
    expense_online = expense_service.expenses_by_method(date_from, date_to)["online"]

    return render_template(
        "expenses.html",
        records=records,
        payment_methods=PAYMENT_METHODS,
        total_purchases=purchase_service.total_purchases(date_from, date_to),
        total_expenses=expense_service.total_expenses(date_from, date_to),
        online_outflow=purchase_online + expense_online,
        date_from=date_from,
        date_to=date_to,
    )


@bp.route("/<int:expense_id>/delete", methods=["POST"])
def delete(expense_id):
    expense_service.delete_expense(expense_id)
    flash("Expense removed.", "success")
    return redirect(url_for("expenses.index"))
