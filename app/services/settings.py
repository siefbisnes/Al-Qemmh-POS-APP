"""
A tiny key/value settings store so an employee can change the warranty
length or low-stock threshold from the Settings page, without anyone
needing to edit config.py and restart the app. Anything not explicitly
set here falls back to the default in config.py - so a brand new
install behaves exactly as before until someone changes a setting.
"""
from flask import current_app

from app.db import db_cursor

DEFINED_SETTINGS = {
    "default_warranty_days": {"label": "Default warranty length (days)", "type": "int"},
    "low_stock_threshold": {"label": "Low stock threshold (units)", "type": "int"},
    "store_address": {"label": "Store address (shown on receipts)", "type": "text"},
    "store_phone": {"label": "Store phone (shown on receipts)", "type": "text"},
    "tax_registration_number": {"label": "Tax registration number (التسجيل الضريبي)", "type": "text"},
    "commercial_register_number": {"label": "Commercial register number (السجل التجاري)", "type": "text"},
    "receipt_policy_text": {"label": "Receipt return/exchange policy notice", "type": "textarea"},
}


def get(key, default=None):
    with db_cursor() as cur:
        row = cur.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None or row["value"] in (None, ""):
        return default
    return row["value"]


def get_int(key, default):
    value = get(key)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def set(key, value):
    with db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def warranty_days():
    return get_int("default_warranty_days", current_app.config["DEFAULT_WARRANTY_DAYS"])


def low_stock_threshold():
    return get_int("low_stock_threshold", current_app.config["LOW_STOCK_THRESHOLD"])


def store_address():
    return get("store_address", "")


def store_phone():
    return get("store_phone", "")


def tax_registration_number():
    return get("tax_registration_number", "")


def commercial_register_number():
    return get("commercial_register_number", "")


def receipt_policy_text():
    return get("receipt_policy_text", "")


def all_settings():
    """Current effective values (DB override if set, else config.py default)."""
    return {
        "default_warranty_days": warranty_days(),
        "low_stock_threshold": low_stock_threshold(),
        "store_address": store_address(),
        "store_phone": store_phone(),
        "tax_registration_number": tax_registration_number(),
        "commercial_register_number": commercial_register_number(),
        "receipt_policy_text": receipt_policy_text(),
    }
