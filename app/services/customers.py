"""
Customer identity and matching.

Customers are now real, persisted rows (see the `customers` table) rather
than groupings computed on the fly from sales.customer_name/customer_phone
text columns. Those text columns still exist on `sales` and are still
written on every sale (receipts, exports, and reports all read them
directly) - `sales.customer_id` is the new link on top that gives
customers a stable identity.

Matching rules (see resolve_customer for the implementation):
  1. Phone match -> use that customer, fill in any missing info.
  2. No phone given, exactly one name match -> use that customer, fill
     in the phone.
  3. Phone+name both match the same customer -> same as (1).
  4. Nothing matches -> create a new customer.
  5. No phone given, MORE THAN ONE name match -> ambiguous. Callers get
     an AmbiguousCustomerError with the candidate list, and must either
     retry with confirmed_customer_id (user picked one) or force_new=True
     (user chose "Create New Customer") before a customer gets attached.

A customer is never created when both name and phone are blank -
resolve_customer simply returns None in that case, and the sale proceeds
without a customer attached.
"""
import sqlite3
from datetime import datetime

from app.db import db_cursor


def _clean(value):
    return (value or "").strip()


def _normalize_name(value):
    return " ".join(_clean(value).replace("/", " ").split()).casefold()


def _money(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


class AmbiguousCustomerError(Exception):
    """Raised by resolve_customer when a name-only sale (no phone) matches
    more than one existing customer and the caller hasn't said which one
    to use (or that a new one should be created instead). Carries the
    candidate list so the caller can show the confirmation dialog."""

    def __init__(self, candidates):
        self.candidates = candidates
        super().__init__("Multiple existing customers match this name.")


# ============================================================
# Matching / resolution (used by the sales checkout flow)
# ============================================================

def _candidate_summary(customer_row):
    customer_id = customer_row["id"]
    with db_cursor() as cur:
        last = cur.execute(
            """SELECT MAX(t.created_at) AS last_date FROM sales s
               JOIN transactions t ON t.id = s.transaction_id
               WHERE s.customer_id = ? AND s.is_voided = 0""",
            (customer_id,),
        ).fetchone()
    return {
        "id": customer_id,
        "name": customer_row["name"] or "",
        "phone": customer_row["phone"] or "",
        "last_purchase_date": last["last_date"] if last else None,
    }


def _missing_fields(candidate, name, phone):
    """Which of the incoming name/phone are non-blank while the matched
    customer's own value is blank - i.e. what _fill_missing would
    actually write. Used to decide whether the user needs to be warned
    before we silently complete their record."""
    missing = []
    if name and not _clean(candidate.get("name")):
        missing.append("name")
    if phone and not _clean(candidate.get("phone")):
        missing.append("phone")
    return missing


def find_name_matches(name):
    """All existing customers whose name matches (case/whitespace
    insensitive). Used both for the ambiguity check and to build the
    Rule 5 confirmation dialog's candidate list."""
    name_norm = _normalize_name(name)
    if not name_norm:
        return []
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM customers WHERE name_normalized = ? ORDER BY updated_at DESC",
            (name_norm,),
        ).fetchall()
    return [_candidate_summary(row) for row in rows]


def check_customer_ambiguity(name, phone):
    """Read-only precheck for the /sales/check-customer AJAX endpoint.
    Never creates or modifies anything - just tells the frontend whether
    it needs to show a dialog before the real submit happens.

    Two things can require a dialog:
      - Rule 5: no phone given, name matches MORE THAN ONE existing
        customer -> "needs_confirmation" (pick one / create new).
      - A phone match, OR - when the phone didn't match anyone (or
        wasn't given) - a single unambiguous name match -> always
        "needs_match_confirmation", even if the matched record already
        has both name and phone filled in. The user typed a name and/or
        phone that already belongs to someone in the system, so this
        sale is about to be recorded under that existing customer -
        they should see who that is and either complete that record's
        missing info or say it's a different person and create a new
        customer, rather than have it happen silently.
        missing_fields lists which of name/phone would additionally get
        filled in on confirm (may be empty if the record was already
        complete).

      A phone match always takes priority over a name match (same
      priority resolve_customer uses) - but if the phone the user typed
      doesn't belong to anyone, the name is still checked, since typing
      an existing customer's name with a new/different number is exactly
      the case this needs to catch, not silently skip."""
    name = _clean(name)
    phone = _clean(phone)
    if not name and not phone:
        return {"status": "ok"}

    if phone:
        with db_cursor() as cur:
            row = cur.execute("SELECT * FROM customers WHERE phone = ?", (phone,)).fetchone()
        if row:
            candidate = _candidate_summary(row)
            missing = _missing_fields(candidate, name, phone)
            return {"status": "needs_match_confirmation", "candidate": candidate, "missing_fields": missing}

    if name:
        candidates = find_name_matches(name)
        if len(candidates) > 1:
            return {"status": "needs_confirmation", "candidates": candidates}
        if len(candidates) == 1:
            missing = _missing_fields(candidates[0], name, phone)
            return {"status": "needs_match_confirmation", "candidate": candidates[0], "missing_fields": missing}
    return {"status": "ok"}


def _fill_missing(cur, row, name, phone):
    """Rules 1-3's "update any missing information" - only fills in
    fields that were previously blank, never overwrites an existing
    value with a different one."""
    updates = {}
    if name and not _clean(row["name"]):
        updates["name"] = name
        updates["name_normalized"] = _normalize_name(name)
    if phone and not _clean(row["phone"]):
        updates["phone"] = phone
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        try:
            cur.execute(
                f"UPDATE customers SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
                (*updates.values(), row["id"]),
            )
        except sqlite3.IntegrityError:
            # Only realistic cause: the phone we're about to fill in
            # already belongs to a different customer. Leave the
            # existing record alone rather than fail the whole sale.
            pass


def update_customer(customer_id, name, phone):
    name = _clean(name)
    phone = _clean(phone)
    if not name and not phone:
        raise ValueError("يرجى إدخال اسم العميل أو رقم الهاتف.")

    name_norm = _normalize_name(name)
    with db_cursor(commit=True) as cur:
        try:
            cur.execute(
                "UPDATE customers SET name = ?, name_normalized = ?, phone = ?, updated_at = datetime('now') WHERE id = ?",
                (name or None, name_norm or None, phone or None, customer_id),
            )
        except sqlite3.IntegrityError:
            raise ValueError("هذا الرقم مستخدم بالفعل من قبل عميل آخر.")
        if cur.rowcount == 0:
            raise ValueError("العميل غير موجود.")


def resolve_customer(name, phone, confirmed_customer_id=None, force_new=False):
    """Implements Rules 1-5. Returns a customer_id, or None if both name
    and phone are blank (nothing to attach).

    Raises AmbiguousCustomerError if name-only input matches more than
    one existing customer and neither confirmed_customer_id nor
    force_new was supplied - the same condition the AJAX precheck
    reports, so this only actually fires if that check was bypassed
    (e.g. JS disabled). Callers should treat it like any other
    validation error: flash a message and let the user retry."""
    name = _clean(name)
    phone = _clean(phone)
    name_norm = _normalize_name(name)

    if not name and not phone:
        return None

    try:
        with db_cursor(commit=True) as cur:
            if confirmed_customer_id:
                row = cur.execute(
                    "SELECT * FROM customers WHERE id = ?", (confirmed_customer_id,)
                ).fetchone()
                if row:
                    _fill_missing(cur, row, name, phone)
                    return row["id"]
                # stale/invalid id - fall through to normal resolution

            if not force_new and phone:
                row = cur.execute("SELECT * FROM customers WHERE phone = ?", (phone,)).fetchone()
                if row:
                    _fill_missing(cur, row, name, phone)
                    return row["id"]

            if not force_new and not phone and name_norm:
                matches = cur.execute(
                    "SELECT * FROM customers WHERE name_normalized = ?", (name_norm,)
                ).fetchall()
                if len(matches) == 1:
                    row = matches[0]
                    _fill_missing(cur, row, name, phone)
                    return row["id"]
                if len(matches) > 1:
                    raise AmbiguousCustomerError([_candidate_summary(r) for r in matches])

            # Rule 4 (nothing matched) or an explicit "Create New Customer".
            cur.execute(
                "INSERT INTO customers (name, name_normalized, phone) VALUES (?, ?, ?)",
                (name or None, name_norm or None, phone or None),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError as exc:
        raise ValueError(
            "Couldn't save this customer - that phone number is already used by another customer."
        ) from exc


# ============================================================
# Reading customers (Customers tab)
# ============================================================

def _purchase_rows_for_customer(customer_id, query=None):
    query_sql = """
        SELECT
            t.id AS transaction_id,
            t.created_at,
            SUM(s.quantity) AS total_quantity,
            SUM(s.quantity * s.selling_price) AS total_amount,
            COALESCE((
                SELECT SUM(sp.amount) FROM sale_payments sp WHERE sp.transaction_id = t.id
            ), 0) AS paid_amount,
            GROUP_CONCAT(COALESCE(NULLIF(TRIM(s.custom_product_name), ''), s.service_description, p.name), ', ') AS product_names
        FROM transactions t
        JOIN sales s ON s.transaction_id = t.id
        JOIN products p ON p.id = s.product_id
        WHERE s.is_voided = 0 AND s.customer_id = ?
        GROUP BY t.id
        ORDER BY t.created_at DESC, t.id DESC
    """
    with db_cursor() as cur:
        rows = [dict(r) for r in cur.execute(query_sql, (customer_id,)).fetchall()]
    for row in rows:
        total_amount_value = float(row["total_amount"] or 0)
        paid_amount_value = float(row["paid_amount"] or 0)
        remaining_amount_value = max(total_amount_value - paid_amount_value, 0)
        row["total_quantity"] = int(row["total_quantity"] or 0)
        row["total_amount_value"] = total_amount_value
        row["paid_amount_value"] = paid_amount_value
        row["remaining_amount_value"] = remaining_amount_value
        row["product_names"] = row["product_names"] or ""
        row["total_amount"] = _money(total_amount_value)
        row["paid_amount"] = _money(paid_amount_value)
        row["remaining_amount"] = _money(remaining_amount_value)

    if query:
        rows = _filter_purchase_rows(rows, query)
    return rows


def _customer_matches_query(customer_name, customer_phone, rows, query):
    q = query.strip().lower()
    if not q:
        return True

    if q in (customer_name or "").lower() or q in (customer_phone or "").lower():
        return True

    qnum = None
    try:
        qnum = float(q)
    except ValueError:
        pass

    for row in rows:
        if q in row["product_names"].lower() or q in row["created_at"].lower():
            return True
        if q in str(row["total_quantity"]):
            return True
        if q in str(row["total_amount"]).lower() or q in str(row["paid_amount"]).lower() or q in str(row["remaining_amount"]).lower():
            return True
        if qnum is not None and (
            abs(row["total_amount_value"] - qnum) < 0.01
            or abs(row["paid_amount_value"] - qnum) < 0.01
            or abs(row["remaining_amount_value"] - qnum) < 0.01
        ):
            return True
    return False


def _filter_purchase_rows(rows, query):
    if not query or not query.strip():
        return rows
    return [row for row in rows if _customer_matches_query("", "", [row], query)]


def list_customers(debtors_only=False, query=None):
    with db_cursor() as cur:
        customer_rows = cur.execute("SELECT * FROM customers ORDER BY updated_at DESC").fetchall()

    customers = []
    for c in customer_rows:
        history = _purchase_rows_for_customer(c["id"])
        if not history:
            continue  # no real purchase history - shouldn't normally happen, but never show a hollow entry

        if query and not _customer_matches_query(c["name"], c["phone"], history, query):
            continue

        total_remaining = sum(r["remaining_amount"] for r in history)
        latest = history[0]  # already ordered newest-first
        customers.append({
            "id": c["id"],
            "customer_name": c["name"] or "",
            "customer_phone": c["phone"] or "",
            "latest_purchase_date": latest["created_at"],
            "last_purchase_total": latest["total_amount"],
            "total_remaining": total_remaining,
            "history": history,
        })

    if debtors_only:
        customers = [c for c in customers if c["total_remaining"] > 0.009]
    customers.sort(key=lambda c: c["latest_purchase_date"], reverse=True)
    return customers


def get_customer(customer_id, query=None):
    with db_cursor() as cur:
        row = cur.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if not row:
        return None

    history = _purchase_rows_for_customer(customer_id, query=query)
    total_remaining = sum(r["remaining_amount"] for r in history)
    total_paid = sum(r["paid_amount"] for r in history)
    total_quantity = sum(r["total_quantity"] for r in history)
    latest = history[0] if history else None

    return {
        "id": row["id"],
        "customer_name": row["name"] or "",
        "customer_phone": row["phone"] or "",
        "latest_purchase_date": latest["created_at"] if latest else row["created_at"],
        "last_purchase_total": latest["total_amount"] if latest else 0.0,
        "total_quantity": total_quantity,
        "total_paid": total_paid,
        "total_remaining": total_remaining,
        "history": history,
    }


def get_purchase(transaction_id):
    with db_cursor() as cur:
        txn = cur.execute(
            "SELECT id, created_at FROM transactions WHERE id = ?",
            (transaction_id,),
        ).fetchone()
        if not txn:
            return None

        lines = [dict(r) for r in cur.execute(
            """
            SELECT
                s.id,
                s.product_id,
                s.quantity,
                s.selling_price,
                s.customer_name,
                s.customer_phone,
                s.customer_id,
                s.sale_date,
                s.service_description,
                s.custom_product_name,
                COALESCE(NULLIF(TRIM(s.custom_product_name), ''), s.service_description, p.name) AS product_name
            FROM sales s
            JOIN products p ON p.id = s.product_id
            WHERE s.transaction_id = ? AND s.is_voided = 0
            ORDER BY s.id
            """,
            (transaction_id,),
        ).fetchall()]
        payments = [dict(r) for r in cur.execute(
            """
            SELECT id, transaction_id, method, amount, created_at
            FROM sale_payments
            WHERE transaction_id = ?
            ORDER BY id
            """,
            (transaction_id,),
        ).fetchall()]

    total = sum(line["quantity"] * line["selling_price"] for line in lines)
    paid = sum(_money(p["amount"]) for p in payments)
    remaining = max(total - paid, 0)
    customer_name = next((line["customer_name"] for line in lines if _clean(line["customer_name"])), "")
    customer_phone = next((line["customer_phone"] for line in lines if _clean(line["customer_phone"])), "")
    customer_id = next((line["customer_id"] for line in lines if line["customer_id"]), None)

    for line in lines:
        line["line_total"] = line["quantity"] * line["selling_price"]

    return {
        "transaction_id": transaction_id,
        "created_at": txn["created_at"],
        "customer_id": customer_id,
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "lines": lines,
        "payments": payments,
        "total": total,
        "paid": paid,
        "remaining": remaining,
    }


def add_payment(transaction_id, amount, method="cash"):
    amount = _money(amount)
    if amount <= 0:
        raise ValueError("Payment amount must be greater than zero.")

    purchase = get_purchase(transaction_id)
    if not purchase:
        raise ValueError("Purchase not found.")

    with db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO sale_payments (transaction_id, method, amount, created_at) VALUES (?, ?, ?, ?)",
            (transaction_id, method, amount, datetime.now().isoformat(timespec="seconds")),
        )

    return get_purchase(transaction_id)
