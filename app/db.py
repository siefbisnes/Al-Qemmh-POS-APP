"""
Thin data-access layer on top of Python's built-in sqlite3 module.

We deliberately do NOT use an ORM. The schema is small and stable, the
queries are straightforward, and the store's "IT support" is likely
whoever set this up - fewer moving parts (no SQLAlchemy, no migration
framework) means fewer ways for it to break on a shop PC. Every service
module in app/services/ talks to the database only through the helpers
below, so swapping this out later (e.g. for SQLAlchemy, or for Postgres
if the store ever grows to multiple branches) only touches this file.
"""
import os
import re
import sqlite3
import json
import threading
from datetime import datetime
from contextlib import contextmanager

from flask import current_app, g
from PIL import Image

# Serializes every committing DB operation across threads (waitress runs
# multiple worker threads, and a sale/quantity-update/etc. can come in from
# more than one request at once). Without this, two writers hitting SQLite
# at the same moment either race or hit "database is locked". This makes
# the second one simply wait its turn - a few milliseconds, invisible to
# whoever's using the app - instead. Read-only cursors (commit=False)
# don't take this lock; only writes need to be serialized.
_write_lock = threading.Lock()

# How long a write will wait for the lock before giving up. Ordinary
# overlapping writes clear in milliseconds, so this essentially never
# fires in normal use - it exists so that if something is ever genuinely
# stuck, the second operation fails clearly (see DatabaseBusyError below)
# instead of hanging indefinitely / "just dying" with no explanation.
DB_WRITE_LOCK_TIMEOUT = 5.0


class DatabaseBusyError(Exception):
    """Raised when a write couldn't get the write lock within
    DB_WRITE_LOCK_TIMEOUT seconds. Caught globally in
    app/utils/errors.py, which turns it into a clear "the operation did
    not happen" message instead of the request hanging or failing with
    a raw error."""


def _row_factory(cursor, row):
    return sqlite3.Row(cursor, row)


def get_db():
    """Return a request-scoped SQLite connection, creating it if needed."""
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE_PATH"],
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def db_cursor(commit=False):
    """Convenience context manager: with db_cursor(commit=True) as cur: ..."""
    conn = get_db()
    acquired = False
    if commit:
        acquired = _write_lock.acquire(timeout=DB_WRITE_LOCK_TIMEOUT)
        if not acquired:
            raise DatabaseBusyError(
                "قاعدة البيانات مشغولة بعملية أخرى في نفس اللحظة - لم يتم تنفيذ العملية. برجاء المحاولة مرة أخرى."
            )
    cur = conn.cursor()
    try:
        yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if commit and acquired:
            _write_lock.release()


def init_app(app):
    app.teardown_appcontext(close_db)


def init_db(app):
    """Create the database file + schema if it doesn't exist yet, and seed
    the five built-in categories on first run. Safe to call every startup."""
    db_path = app.config["DATABASE_PATH"]
    schema_path = app.config["SCHEMA_PATH"]

    os.makedirs(db_path.rsplit(os.sep, 1)[0], exist_ok=True)
    os.makedirs(app.config["PRODUCT_IMAGES_DIR"], exist_ok=True)
    os.makedirs(app.config["TMP_DIR"], exist_ok=True)

    is_new = not os.path.exists(db_path)
    needs_schema_init = is_new

    if not is_new:
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("SELECT name FROM sqlite_master LIMIT 1")
            conn.close()
        except sqlite3.DatabaseError as exc:
            _backup_corrupt_database(db_path)
            needs_schema_init = True

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    with open(schema_path, encoding="utf-8") as f:
        schema_sql = f.read()

    if needs_schema_init:
        conn.executescript(schema_sql)
        conn.commit()
    run_migrations(conn)

    _migrate_redundant_identifier_fields(conn)
    _migrate_gpu_memory_type_options(conn)
    _migrate_backfill_customers(conn)
    _migrate_convert_images_to_webp(conn, app.config["PRODUCT_IMAGES_DIR"])
    conn.close()

    if needs_schema_init or is_new:
        from app.services import categories as category_service
        with app.app_context():
            category_service.seed_builtin_categories()


def _backup_corrupt_database(db_path):
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = f"{db_path}.corrupt-{timestamp}"
    try:
        os.replace(db_path, backup_path)
    except FileNotFoundError:
        return backup_path
    except OSError:
        try:
            os.remove(db_path)
        except OSError:
            pass
    return backup_path


def _migrate_redundant_identifier_fields(conn):
    """One-time data migration: Monitors/GPUs/CPUs/PCs used to also have a
    per-category "Model" spec field, and Accessories a "Name" spec field,
    duplicating the top-level product Model field. Any value already
    entered there is copied onto the product's own Model field first (only
    if that field looks empty, so a deliberately different name is never
    overwritten), then the now-redundant spec field is removed."""
    cur = conn.cursor()
    targets = [
        ("Monitors", "model"), ("GPUs", "model"), ("CPUs", "model"),
        ("PCs / Workstations", "model"), ("Accessories", "name"),
    ]
    changed = False
    for category_name, field_key in targets:
        cat = cur.execute("SELECT id FROM categories WHERE name = ?", (category_name,)).fetchone()
        if not cat:
            continue
        field = cur.execute(
            "SELECT id FROM category_fields WHERE category_id = ? AND field_key = ?",
            (cat[0], field_key),
        ).fetchone()
        if not field:
            continue
        field_id = field[0]
        specs = cur.execute(
            "SELECT product_id, value FROM specifications WHERE category_field_id = ?", (field_id,)
        ).fetchall()
        for product_id, value in specs:
            if value:
                row = cur.execute("SELECT name FROM products WHERE id = ?", (product_id,)).fetchone()
                if row and not (row[0] or "").strip():
                    cur.execute("UPDATE products SET name = ? WHERE id = ?", (value, product_id))
        cur.execute("DELETE FROM category_fields WHERE id = ?", (field_id,))  # cascades specifications
        changed = True
    if changed:
        conn.commit()


def _migrate_gpu_memory_type_options(conn):
    cur = conn.cursor()
    cat = cur.execute("SELECT id FROM categories WHERE name = 'GPUs'").fetchone()
    if not cat:
        return
    field = cur.execute(
        "SELECT id, field_options FROM category_fields WHERE category_id = ? AND field_key = 'memory_type'",
        (cat[0],),
    ).fetchone()
    if not field:
        return
    new_options = json.dumps(["DDR3", "DDR5", "DDR6", "DDR6X", "Other"])
    if field[1] != new_options:
        cur.execute("UPDATE category_fields SET field_options = ? WHERE id = ?", (new_options, field[0]))
        conn.commit()


def _normalize_customer_name(value):
    """Same normalization used by app/services/customers.py - lowercased,
    slashes treated as spaces, whitespace collapsed. Kept as a standalone
    copy here (rather than importing the service) since this module must
    stay import-light and usable before the Flask app context exists."""
    value = (value or "").strip().replace("/", " ")
    return " ".join(value.split()).casefold()


def _migrate_backfill_customers(conn):
    """One-time data migration: before the customers table existed,
    "customers" were purely virtual - grouped at query time from
    sales.customer_name / sales.customer_phone. This walks every sale
    that has a name or phone but no customer_id yet, oldest first, and
    resolves/creates real customer rows using the same matching rules
    the app now applies going forward (phone match first, then exact
    normalized-name match). Only touches sales.customer_id - the
    original customer_name/customer_phone text columns are untouched.

    Idempotent: only ever looks at rows where customer_id IS NULL, so
    re-running after a previous partial run (or on every startup) is
    safe and just picks up anything new.

    Ambiguous name-only matches (multiple existing customers with the
    same name, no phone to disambiguate) deliberately create a new
    customer rather than guessing which one - there's no user present
    during a migration to ask, and guessing wrong would silently merge
    two different people's purchase history."""
    cur = conn.cursor()

    has_customers_table = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='customers'"
    ).fetchone()
    if not has_customers_table:
        return  # migrations haven't created it yet this run - nothing to backfill

    columns = [row[1] for row in cur.execute("PRAGMA table_info(sales)").fetchall()]
    if "customer_id" not in columns:
        return

    rows = cur.execute(
        """
        SELECT id, customer_name, customer_phone, sale_date, created_at
        FROM sales
        WHERE customer_id IS NULL
          AND (TRIM(COALESCE(customer_name, '')) <> '' OR TRIM(COALESCE(customer_phone, '')) <> '')
        ORDER BY COALESCE(sale_date, created_at) ASC, id ASC
        """
    ).fetchall()
    if not rows:
        return

    now_changed = False
    for sale_id, name, phone, sale_date, created_at in rows:
        name = (name or "").strip()
        phone = (phone or "").strip()
        name_norm = _normalize_customer_name(name)

        customer_id = None

        if phone:
            existing = cur.execute("SELECT id, name FROM customers WHERE phone = ?", (phone,)).fetchone()
            if existing:
                customer_id = existing[0]
                if name and not (existing[1] or "").strip():
                    cur.execute(
                        "UPDATE customers SET name = ?, name_normalized = ?, updated_at = datetime('now') WHERE id = ?",
                        (name, name_norm, customer_id),
                    )

        if customer_id is None and not phone and name_norm:
            matches = cur.execute(
                "SELECT id FROM customers WHERE name_normalized = ? AND (phone IS NULL OR phone = '')",
                (name_norm,),
            ).fetchall()
            if len(matches) == 1:
                customer_id = matches[0][0]

        if customer_id is None:
            cur.execute(
                "INSERT INTO customers (name, name_normalized, phone) VALUES (?, ?, ?)",
                (name or None, name_norm or None, phone or None),
            )
            customer_id = cur.lastrowid
        elif phone:
            # Existing customer matched by phone but had a blank phone
            # recorded some other way (shouldn't normally happen since we
            # matched ON phone, but keeps this branch defensive/no-op safe).
            pass

        cur.execute("UPDATE sales SET customer_id = ? WHERE id = ?", (customer_id, sale_id))
        now_changed = True

    if now_changed:
        conn.commit()


def _migrate_convert_images_to_webp(conn, images_dir):
    """One-time-per-image migration: convert every existing product photo
    to WebP (smaller files -> faster catalog/product page loads) and
    replace the original file on disk.

    Idempotent and incremental by construction: the query only selects
    rows whose filepath doesn't already end in '.webp', so once a photo
    has been converted it is never touched again. On a store's normal
    startup - after the first run has already converted everything -
    this is a single cheap SELECT that returns zero rows, so it does not
    slow down the app.

    A failure on any single image (corrupt file, permissions, etc.) is
    caught and that image is skipped, left exactly as it was - one bad
    file can never break startup or block conversion of the rest."""
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, filepath FROM product_images WHERE filepath NOT LIKE '%.webp'"
    ).fetchall()
    if not rows:
        return

    changed = False
    for image_id, filepath in rows:
        old_full = os.path.join(images_dir, filepath)
        if not os.path.exists(old_full):
            continue

        base, _ext = os.path.splitext(filepath)
        new_filepath = f"{base}.webp"
        new_full = os.path.join(images_dir, new_filepath)
        counter = 1
        while os.path.exists(new_full):
            new_filepath = f"{base}-{counter}.webp"
            new_full = os.path.join(images_dir, new_filepath)
            counter += 1

        try:
            with Image.open(old_full) as img:
                if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                    img = img.convert("RGBA")
                else:
                    img = img.convert("RGB")
                if max(img.size) > 1600:
                    img.thumbnail((1600, 1600))
                img.save(new_full, "WEBP", quality=82, method=6)
        except Exception:
            if os.path.exists(new_full):
                try:
                    os.remove(new_full)
                except OSError:
                    pass
            continue

        try:
            os.remove(old_full)
        except OSError:
            pass
        cur.execute("UPDATE product_images SET filepath = ? WHERE id = ?", (new_filepath, image_id))
        changed = True

    if changed:
        conn.commit()


# Each entry: (description, SQL). Run in order, every startup, on every
# database - old or brand new. Each statement must be safe to run twice
# (idempotent), since a freshly-created DB from schema.sql may already
# have some of these and a long-running shop DB needs the rest applied.
# This is how new features get added to an existing store's database
# without ever touching their existing data.
MIGRATIONS = [
    ("settings table", """
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """),
    ("transactions table", """
        CREATE TABLE IF NOT EXISTS transactions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            receipt_requested INTEGER NOT NULL DEFAULT 0
        )
    """),
    ("sale_payments table", """
        CREATE TABLE IF NOT EXISTS sale_payments (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
            method         TEXT NOT NULL,
            amount         REAL NOT NULL
        )
    """),
    ("sale_payments.created_at column", """
        ALTER TABLE sale_payments ADD COLUMN created_at TEXT
    """),
    ("sale_payments.created_at backfill", """
        UPDATE sale_payments
        SET created_at = COALESCE(
            created_at,
            (SELECT t.created_at FROM transactions t WHERE t.id = sale_payments.transaction_id),
            datetime('now')
        )
        WHERE created_at IS NULL OR TRIM(created_at) = ''
    """),
    ("sales.transaction_id column", """
        ALTER TABLE sales ADD COLUMN transaction_id INTEGER REFERENCES transactions(id)
    """),
    ("sales.is_voided column", """
        ALTER TABLE sales ADD COLUMN is_voided INTEGER NOT NULL DEFAULT 0
    """),
    ("sales.voided_at column", """
        ALTER TABLE sales ADD COLUMN voided_at TEXT
    """),
    ("sales.warranty_days column", """
        ALTER TABLE sales ADD COLUMN warranty_days INTEGER
    """),
    ("sales.service_description column", """
        ALTER TABLE sales ADD COLUMN service_description TEXT
    """),
    ("sales.custom_product_name column", """
        ALTER TABLE sales ADD COLUMN custom_product_name TEXT
    """),
    ("idx_sales_transaction", """
        CREATE INDEX IF NOT EXISTS idx_sales_transaction ON sales(transaction_id)
    """),
    ("idx_sales_customer_phone", """
        CREATE INDEX IF NOT EXISTS idx_sales_customer_phone ON sales(customer_phone)
    """),
    ("idx_sales_customer_name", """
        CREATE INDEX IF NOT EXISTS idx_sales_customer_name ON sales(customer_name)
    """),
    ("idx_sales_customer_phone_name", """
        CREATE INDEX IF NOT EXISTS idx_sales_customer_phone_name ON sales(customer_phone, customer_name)
    """),
    ("idx_sale_payments_created_at", """
        CREATE INDEX IF NOT EXISTS idx_sale_payments_created_at ON sale_payments(created_at)
    """),
    ("idx_payments_transaction", """
        CREATE INDEX IF NOT EXISTS idx_payments_transaction ON sale_payments(transaction_id)
    """),
    ("expenses table", """
        CREATE TABLE IF NOT EXISTS expenses (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            description    TEXT NOT NULL,
            amount         REAL NOT NULL,
            payment_method TEXT NOT NULL DEFAULT 'cash',
            expense_date   TEXT NOT NULL DEFAULT (datetime('now')),
            created_at     TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """),
    ("idx_expenses_date", """
        CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(expense_date)
    """),
    ("expenses payment_method column", """
        ALTER TABLE expenses ADD COLUMN payment_method TEXT NOT NULL DEFAULT 'cash'
    """),
    ("purchases table", """
        CREATE TABLE IF NOT EXISTS purchases (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT NOT NULL,
            cost           REAL NOT NULL,
            payment_method TEXT NOT NULL DEFAULT 'cash',
            purchase_date  TEXT NOT NULL DEFAULT (datetime('now')),
            created_at     TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """),
    ("idx_purchases_date", """
        CREATE INDEX IF NOT EXISTS idx_purchases_date ON purchases(purchase_date)
    """),
    ("manual_adjustments table", """
        CREATE TABLE IF NOT EXISTS manual_adjustments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            target          TEXT NOT NULL,
            amount          REAL NOT NULL,
            note            TEXT,
            adjustment_date TEXT NOT NULL DEFAULT (datetime('now')),
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """),
    ("idx_adjustments_target", """
        CREATE INDEX IF NOT EXISTS idx_adjustments_target ON manual_adjustments(target)
    """),
    ("idx_adjustments_date", """
        CREATE INDEX IF NOT EXISTS idx_adjustments_date ON manual_adjustments(adjustment_date)
    """),
    ("customers table", """
        CREATE TABLE IF NOT EXISTS customers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT,
            name_normalized TEXT,
            phone           TEXT UNIQUE,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """),
    ("idx_customers_name_normalized", """
        CREATE INDEX IF NOT EXISTS idx_customers_name_normalized ON customers(name_normalized)
    """),
    ("sales.customer_id column", """
        ALTER TABLE sales ADD COLUMN customer_id INTEGER REFERENCES customers(id)
    """),
    ("idx_sales_customer_id", """
        CREATE INDEX IF NOT EXISTS idx_sales_customer_id ON sales(customer_id)
    """),
    ("stock_writeoffs table", """
        CREATE TABLE IF NOT EXISTS stock_writeoffs (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id              INTEGER NOT NULL REFERENCES products(id),
            quantity                INTEGER NOT NULL,
            purchase_price          REAL NOT NULL DEFAULT 0,
            selling_price           REAL NOT NULL DEFAULT 0,
            cost_loss               REAL NOT NULL DEFAULT 0,
            revenue_loss            REAL NOT NULL DEFAULT 0,
            note                    TEXT,
            writeoff_date           TEXT NOT NULL DEFAULT (datetime('now')),
            created_at              TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """),
    ("idx_writeoffs_product", """
        CREATE INDEX IF NOT EXISTS idx_writeoffs_product ON stock_writeoffs(product_id)
    """),
    ("idx_writeoffs_date", """
        CREATE INDEX IF NOT EXISTS idx_writeoffs_date ON stock_writeoffs(writeoff_date)
    """),
    # Client-requested override: spreads a write-off's cost_loss over 12
    # monthly installments instead of expensing it all in one month. See
    # docs/spoilage_management_requirements.md and app/services/writeoffs.py.
    ("spoilage_amortization table", """
        CREATE TABLE IF NOT EXISTS spoilage_amortization (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            writeoff_id         INTEGER NOT NULL REFERENCES stock_writeoffs(id) ON DELETE CASCADE,
            product_id          INTEGER NOT NULL REFERENCES products(id),
            installment_number  INTEGER NOT NULL,
            period              TEXT NOT NULL,
            amount              REAL NOT NULL,
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(writeoff_id, installment_number)
        )
    """),
    ("idx_spoilage_amort_period", """
        CREATE INDEX IF NOT EXISTS idx_spoilage_amort_period ON spoilage_amortization(period)
    """),
    ("idx_spoilage_amort_writeoff", """
        CREATE INDEX IF NOT EXISTS idx_spoilage_amort_writeoff ON spoilage_amortization(writeoff_id)
    """),
    # ---- Delivery orders (طلبات التوصيل) ----
    ("orders table", """
        CREATE TABLE IF NOT EXISTS orders (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id            INTEGER NOT NULL UNIQUE REFERENCES transactions(id) ON DELETE CASCADE,
            delivery_provider         TEXT NOT NULL,
            status                    TEXT NOT NULL DEFAULT 'preparing',
            order_amount              REAL NOT NULL DEFAULT 0,
            payment_method            TEXT,
            money_transferred         INTEGER NOT NULL DEFAULT 0,
            transfer_image_path       TEXT,
            financially_completed_at  TEXT,
            created_at                TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at                TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """),
    ("idx_orders_transaction", """
        CREATE INDEX IF NOT EXISTS idx_orders_transaction ON orders(transaction_id)
    """),
    ("idx_orders_status", """
        CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)
    """),
    ("order_status_history table", """
        CREATE TABLE IF NOT EXISTS order_status_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            from_status TEXT,
            to_status   TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """),
    ("idx_order_status_history_order", """
        CREATE INDEX IF NOT EXISTS idx_order_status_history_order ON order_status_history(order_id)
    """),
    ("delivery_providers table", """
        CREATE TABLE IF NOT EXISTS delivery_providers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            slug        TEXT NOT NULL UNIQUE,
            name        TEXT NOT NULL,
            is_builtin  INTEGER NOT NULL DEFAULT 0,
            sort_order  INTEGER NOT NULL DEFAULT 0
        )
    """),
    ("delivery_providers seed", """
        INSERT OR IGNORE INTO delivery_providers (slug, name, is_builtin, sort_order) VALUES
            ('egypt_post', 'البريد المصري', 1, 1),
            ('bosta', 'بوسطة', 1, 2),
            ('raymond', 'رايموند', 1, 3)
    """),
    # ---- Shipping-cost accounting (separate from order revenue) ----
    ("orders.shipping_cost", """
        ALTER TABLE orders ADD COLUMN shipping_cost REAL NOT NULL DEFAULT 0
    """),
    ("orders.shipping_cost_source", """
        ALTER TABLE orders ADD COLUMN shipping_cost_source TEXT NOT NULL DEFAULT 'drawer'
    """),
    ("orders.shipping_purchase_id", """
        ALTER TABLE orders ADD COLUMN shipping_purchase_id INTEGER REFERENCES purchases(id) ON DELETE SET NULL
    """),
    ("orders.delivered_at", """
        ALTER TABLE orders ADD COLUMN delivered_at TEXT
    """),
    ("orders.tracking_number", """
        ALTER TABLE orders ADD COLUMN tracking_number TEXT
    """),
    ("orders.shipping_offset_purchase_id", """
        ALTER TABLE orders ADD COLUMN shipping_offset_purchase_id INTEGER REFERENCES purchases(id) ON DELETE SET NULL
    """),
    # ---- Product audit log (see schema.sql for the full column-by-
    # column rationale) ----
    ("product_audit_log table", """
        CREATE TABLE IF NOT EXISTS product_audit_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id      INTEGER REFERENCES products(id) ON DELETE SET NULL,
            product_name    TEXT NOT NULL,
            event_type      TEXT NOT NULL,
            field           TEXT,
            old_value       TEXT,
            new_value       TEXT,
            quantity_before INTEGER,
            quantity_after  INTEGER,
            reference       TEXT,
            reference_type  TEXT,
            note            TEXT,
            username        TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """),
    ("idx_product_audit_product", """
        CREATE INDEX IF NOT EXISTS idx_product_audit_product ON product_audit_log(product_id)
    """),
    ("idx_product_audit_created", """
        CREATE INDEX IF NOT EXISTS idx_product_audit_created ON product_audit_log(created_at)
    """),
    ("idx_product_audit_event", """
        CREATE INDEX IF NOT EXISTS idx_product_audit_event ON product_audit_log(event_type)
    """),
    # CREATE TABLE IF NOT EXISTS above is a no-op on a database that
    # already has product_audit_log from before reference_type existed
    # - this ALTER TABLE is what actually adds the column there. The
    # runner tolerates a "duplicate column" error on databases that
    # already have it (either from a fresh CREATE TABLE above, or a
    # previous run of this exact migration), so this is safe to run
    # every startup.
    ("product_audit_log.reference_type", """
        ALTER TABLE product_audit_log ADD COLUMN reference_type TEXT
    """),
    # ---- Stored, collision-safe receipt numbers (see schema.sql for
    # the full rationale) - replaces computing INV-<year>-<id> from the
    # transactions.id primary key on the fly, which breaks if two
    # databases are ever merged (id sequences can collide across
    # separate databases; a randomly-generated stored code cannot). ----
    ("transactions.receipt_number", """
        ALTER TABLE transactions ADD COLUMN receipt_number TEXT
    """),
    ("idx_transactions_receipt_number", """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_receipt_number
        ON transactions(receipt_number) WHERE receipt_number IS NOT NULL
    """),
    # ---- Expected Returns daily snapshot (see schema.sql for the full
    # rationale) - no backfill inserted here on purpose: an old database
    # simply has no rows before today, which is correct, not a bug. ----
    ("expected_returns_daily table", """
        CREATE TABLE IF NOT EXISTS expected_returns_daily (
            snapshot_date TEXT PRIMARY KEY,
            value         REAL NOT NULL DEFAULT 0,
            updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """),
]


def run_migrations(conn):
    for description, sql in MIGRATIONS:
        try:
            conn.execute(sql)
            conn.commit()
        except sqlite3.OperationalError as e:
            # "duplicate column" / already exists -> already applied, fine.
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                conn.rollback()
                continue
            raise RuntimeError(f"Migration failed ({description}): {e}") from e
