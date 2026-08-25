-- Al-Qemma Inventory & Sales Management System
-- SQLite schema
-- All foreign keys are enforced (PRAGMA foreign_keys = ON, set in app/db.py per connection)

-- ============================================================
-- CATEGORIES + DYNAMIC FIELDS
-- This pair of tables is what lets the store add a brand new
-- product category (e.g. "Laptops", "Printers") later WITHOUT
-- any schema change / migration. A category is just a row, and
-- its fields are just rows in category_fields.
-- ============================================================

CREATE TABLE categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,        -- e.g. "Monitors"
    slug        TEXT NOT NULL UNIQUE,        -- e.g. "monitors"
    is_builtin  INTEGER NOT NULL DEFAULT 0,  -- informational only, builtin categories can still be edited
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE category_fields (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id   INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    field_key     TEXT NOT NULL,             -- machine name, e.g. "screen_size"
    field_label   TEXT NOT NULL,             -- shown to the user, e.g. "Screen Size"
    field_type    TEXT NOT NULL DEFAULT 'text',  -- text | number | select | textarea
    field_options TEXT,                      -- JSON array of choices, only used when field_type = 'select'
    is_required   INTEGER NOT NULL DEFAULT 0,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    UNIQUE(category_id, field_key)
);

-- ============================================================
-- PRODUCTS
-- ============================================================

CREATE TABLE products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id     INTEGER NOT NULL REFERENCES categories(id),
    name            TEXT NOT NULL,
    grade           TEXT NOT NULL DEFAULT 'A',   -- A / B / C / Scrap, etc. (free text, kept simple)
    quantity        INTEGER NOT NULL DEFAULT 0,
    description     TEXT,
    purchase_price  REAL NOT NULL DEFAULT 0,
    selling_price   REAL NOT NULL DEFAULT 0,
    date_added      TEXT NOT NULL DEFAULT (datetime('now')),
    is_active       INTEGER NOT NULL DEFAULT 1,  -- soft delete flag
    source          TEXT NOT NULL DEFAULT 'manual'
);

CREATE INDEX idx_products_name ON products(name);
CREATE INDEX idx_products_category ON products(category_id);
CREATE INDEX idx_products_active ON products(is_active);

-- ============================================================
-- PRODUCT IMAGES
-- ============================================================

CREATE TABLE product_images (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id              INTEGER REFERENCES products(id) ON DELETE CASCADE,
    filepath                TEXT NOT NULL,      -- relative path under instance/product_images
    is_primary              INTEGER NOT NULL DEFAULT 0,
    sort_order              INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_images_product ON product_images(product_id);

-- ============================================================
-- SPECIFICATIONS (EAV value table — the actual per-product
-- values for whatever fields its category defines)
-- ============================================================

CREATE TABLE specifications (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id        INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    category_field_id INTEGER NOT NULL REFERENCES category_fields(id) ON DELETE CASCADE,
    value             TEXT,
    UNIQUE(product_id, category_field_id)
);

CREATE INDEX idx_specs_product ON specifications(product_id);

-- ============================================================
-- COMPATIBILITY
-- Always manually entered, never inferred. product_id is the
-- "host" product (e.g. a PC/Workstation) and component_value is
-- the compatible part (e.g. "E5-1650 V3"). Searchable both ways.
-- ============================================================

CREATE TABLE compatibility (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    component_type  TEXT NOT NULL,   -- e.g. "CPU", "RAM", "Storage", "PSU"
    component_value TEXT NOT NULL,   -- e.g. "E5-1650 V3"
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_compat_product ON compatibility(product_id);
CREATE INDEX idx_compat_value ON compatibility(component_value);

-- ============================================================
-- CUSTOMERS
-- Real, persisted customer identity - not derived at query time.
-- phone is UNIQUE (SQLite allows any number of NULLs alongside a
-- UNIQUE constraint, so multiple customers with no phone are
-- fine). name_normalized is a lowercased/whitespace-collapsed
-- copy of name, used purely for matching so "Ahmed  Mohamed" and
-- "ahmed mohamed" are recognized as the same typed name.
-- ============================================================

CREATE TABLE customers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT,
    name_normalized TEXT,
    phone           TEXT UNIQUE,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_customers_name_normalized ON customers(name_normalized);

-- ============================================================
-- SALES
-- ============================================================

CREATE TABLE sales (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(id),
    sale_date       TEXT NOT NULL DEFAULT (datetime('now')),
    selling_price   REAL NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 1,
    customer_name   TEXT,
    customer_phone  TEXT,
    customer_id     INTEGER REFERENCES customers(id),
    transaction_id  INTEGER REFERENCES transactions(id),
    is_voided       INTEGER NOT NULL DEFAULT 0,
    voided_at       TEXT,
    warranty_days   INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_sales_product ON sales(product_id);
CREATE INDEX idx_sales_date ON sales(sale_date);
CREATE INDEX idx_sales_transaction ON sales(transaction_id);
CREATE INDEX idx_sales_customer_phone ON sales(customer_phone);
CREATE INDEX idx_sales_customer_id ON sales(customer_id);

-- ============================================================
-- TRANSACTIONS + PAYMENTS
-- One "Selling" session can contain several sale lines (different
-- products, same customer). They all share a transaction_id so a
-- receipt can list every line under one combined payment summary.
-- A payment can be split across multiple methods (cash + Vodafone
-- Cash + Instapay all in the same transaction).
-- ============================================================

CREATE TABLE transactions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    receipt_requested INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE sale_payments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    method         TEXT NOT NULL,   -- cash | vodafone_cash | instapay
    amount         REAL NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_payments_transaction ON sale_payments(transaction_id);
CREATE INDEX idx_sale_payments_created_at ON sale_payments(created_at);

-- ============================================================
-- SETTINGS (simple key/value store, e.g. default warranty days,
-- low-stock threshold - editable from the Settings page instead of
-- requiring a developer to edit config.py)
-- ============================================================

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- ============================================================
-- EXPENSES (general business costs - rent, utilities, salaries,
-- etc. - separate from a product's purchase price/COGS)
-- ============================================================

CREATE TABLE expenses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    description    TEXT NOT NULL,
    amount         REAL NOT NULL,
    payment_method TEXT NOT NULL DEFAULT 'cash',
    expense_date   TEXT NOT NULL DEFAULT (datetime('now')),
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_expenses_date ON expenses(expense_date);

-- ============================================================
-- WARRANTIES
-- ============================================================

CREATE TABLE warranties (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id         INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
    start_date      TEXT NOT NULL,
    expiration_date TEXT NOT NULL
);

CREATE INDEX idx_warranties_expiration ON warranties(expiration_date);

-- ============================================================
-- PURCHASES
-- General cost outflow - not just product restocking, but rent,
-- utilities, anything paid out of the shop's cash or online
-- balance. payment_method drives which side of the Reports page
-- (Cash vs Online) the amount gets subtracted from.
-- ============================================================

CREATE TABLE purchases (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    cost           REAL NOT NULL,
    payment_method TEXT NOT NULL DEFAULT 'cash',
    purchase_date  TEXT NOT NULL DEFAULT (datetime('now')),
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_purchases_date ON purchases(purchase_date);

CREATE TABLE manual_adjustments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target          TEXT NOT NULL,   -- drawer | today | total | expenses | purchases | net_profit
    amount          REAL NOT NULL,   -- positive = add, negative = remove
    note            TEXT,
    adjustment_date TEXT NOT NULL DEFAULT (datetime('now')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_adjustments_target ON manual_adjustments(target);
CREATE INDEX idx_adjustments_date ON manual_adjustments(adjustment_date);

-- ============================================================
-- STOCK WRITEOFFS (liquidity / damage losses)
-- Explicit ledger for units removed as damaged/spoiled/dead stock.
-- Snapshots purchase + selling price at write-off time so owner
-- dashboards can show cost loss and forgone revenue. Decrements
-- products.quantity in the same transaction.
-- ============================================================

CREATE TABLE stock_writeoffs (
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
);
CREATE INDEX idx_writeoffs_product ON stock_writeoffs(product_id);
CREATE INDEX idx_writeoffs_date ON stock_writeoffs(writeoff_date);

-- ============================================================
-- SPOILAGE AMORTIZATION SCHEDULE
-- CUSTOM CLIENT-REQUESTED OVERRIDE of standard accounting: a write-off's
-- cost_loss is normally expensed in full in the month it happens. Per
-- client request, it is instead spread evenly across 12 monthly
-- installments and posted to Net Profit one installment at a time. All
-- 12 rows for a write-off are generated up front, one per calendar month
-- starting with the write-off's own month - there is deliberately no row
-- for month 13+, so a write-off's effect on Net Profit stops after its
-- 12th installment by construction, not by a runtime date check.
-- See docs/spoilage_management_requirements.md.
-- ============================================================
CREATE TABLE spoilage_amortization (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    writeoff_id         INTEGER NOT NULL REFERENCES stock_writeoffs(id) ON DELETE CASCADE,
    product_id          INTEGER NOT NULL REFERENCES products(id),
    installment_number  INTEGER NOT NULL,      -- 1..12
    period              TEXT NOT NULL,         -- 'YYYY-MM', the month this installment counts against
    amount              REAL NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(writeoff_id, installment_number)
);

CREATE INDEX idx_spoilage_amort_period ON spoilage_amortization(period);
CREATE INDEX idx_spoilage_amort_writeoff ON spoilage_amortization(writeoff_id);
-- ============================================================
-- DELIVERY ORDERS (طلبات التوصيل)
-- Append to schema.sql. Additive only — every existing table is
-- untouched, so this works against any current working database
-- (just run this block once, e.g. via a tiny migration script or
-- `sqlite3 alqemma.db < schema_orders_addition.sql`).
--
-- One order == one transaction (1:1). The order NEVER duplicates
-- financial data that already lives on transactions/sales/
-- sale_payments — order_amount is a convenience snapshot of the
-- transaction total at creation time (for list display without a
-- join), everything else (lines, customer, payments) is read
-- straight from the existing tables via transaction_id.
--
-- financially_completed_at is the single flag every report /
-- owner-dashboard query keys off: NULL = pending money, not yet
-- counted anywhere. Set once, on the وصل + payment-confirmation
-- step, and never cleared again.
-- ============================================================

CREATE TABLE orders (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id          INTEGER NOT NULL UNIQUE REFERENCES transactions(id) ON DELETE CASCADE,
    delivery_provider       TEXT NOT NULL,          -- e.g. 'egypt_post' | 'bosta' | 'raymond' | custom
    status                  TEXT NOT NULL DEFAULT 'preparing',
        -- preparing | shipping | delivered | not_delivered | cancelled
    order_amount            REAL NOT NULL DEFAULT 0,  -- snapshot of transaction total at creation
    payment_method          TEXT,                    -- cash | vodafone_cash | instapay (set at وصل)
    money_transferred        INTEGER NOT NULL DEFAULT 0,
    transfer_image_path     TEXT,
    financially_completed_at TEXT,                   -- NULL until money is recognized in reports
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_orders_transaction ON orders(transaction_id);
CREATE INDEX idx_orders_status ON orders(status);

-- Lightweight audit trail for the 3-stage progress control + terminal
-- states. Not required for the feature to work (orders.status is the
-- source of truth) but cheap and useful for support/debugging.
CREATE TABLE order_status_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_order_status_history_order ON order_status_history(order_id);

-- Extensible delivery-provider list (see spec §5: adding a company
-- later must not require a schema/design change). Seeded with the
-- three initial providers; is_builtin is informational only, same
-- convention as categories.is_builtin.
CREATE TABLE delivery_providers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    is_builtin  INTEGER NOT NULL DEFAULT 0,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

INSERT INTO delivery_providers (slug, name, is_builtin, sort_order) VALUES
    ('egypt_post', 'البريد المصري', 1, 1),
    ('bosta', 'بوسطة', 1, 2),
    ('raymond', 'رايموند', 1, 3);
