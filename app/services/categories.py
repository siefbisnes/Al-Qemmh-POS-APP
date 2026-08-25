"""
Categories own a set of "fields" (category_fields) instead of the database
having one column per spec per category. This is the whole trick that lets
the store add a brand new category later (e.g. "Laptops") from the UI,
with its own custom fields, and never touch the schema or write code.
"""
import json
import re

from app.db import db_cursor

# (name, [ (field_key, field_label, field_type, options, is_required), ... ])
# NOTE: there is deliberately no "model" field in any of these. The product's
# top-level "Model" field (what used to be "Product Name") is the single
# identifier now - employees enter it once, not once on the product and
# again as a spec.
BUILTIN_CATEGORIES = [
    ("Monitors", [
        ("brand", "Brand", "text", None, True),
        ("screen_size", "Screen Size", "text", None, True),
        ("resolution", "Resolution", "text", None, True),
        ("refresh_rate", "Refresh Rate", "text", None, True),
        ("panel_type", "Panel Type", "select", ["IPS", "VA", "TN", "OLED"], True),
        ("ports", "Ports", "text", None, False),
    ]),
    ("GPUs", [
        ("brand", "Brand", "text", None, True),
        ("gpu_chip", "GPU Chip", "text", None, True),
        ("vram", "VRAM", "text", None, True),
        ("memory_type", "Memory Type", "select", ["DDR3", "DDR5", "DDR6", "DDR6X", "Other"], False),
        ("power_consumption", "Power Consumption", "text", None, False),
        ("ports", "Ports", "text", None, False),
    ]),
    ("CPUs", [
        ("brand", "Brand", "select", ["Intel", "AMD"], True),
        ("socket", "Socket", "text", None, True),
        ("cores", "Cores", "number", None, True),
        ("threads", "Threads", "number", None, False),
        ("frequency", "Frequency", "text", None, False),
        ("generation", "Generation", "text", None, False),
    ]),
    ("PCs / Workstations", [
        ("brand", "Brand", "text", None, True),
        ("cpu", "CPU", "text", None, True),
        ("ram", "RAM", "text", None, True),
        ("storage", "Storage", "text", None, True),
        ("gpu", "GPU", "text", None, False),
        ("power_supply", "Power Supply", "text", None, False),
        ("motherboard", "Motherboard", "text", None, False),
    ]),
    ("Accessories", [
        ("description", "Description", "textarea", None, False),
    ]),
]


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "category"


def seed_builtin_categories():
    with db_cursor(commit=True) as cur:
        existing = {r["slug"] for r in cur.execute("SELECT slug FROM categories").fetchall()}
        for order, (name, fields) in enumerate(BUILTIN_CATEGORIES):
            slug = slugify(name)
            if slug in existing:
                continue
            cur.execute(
                "INSERT INTO categories (name, slug, is_builtin, sort_order) VALUES (?, ?, 1, ?)",
                (name, slug, order),
            )
            category_id = cur.lastrowid
            for f_order, (key, label, ftype, options, required) in enumerate(fields):
                cur.execute(
                    """INSERT INTO category_fields
                       (category_id, field_key, field_label, field_type, field_options, is_required, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (category_id, key, label, ftype, json.dumps(options) if options else None,
                     int(required), f_order),
                )


def list_categories(include_fields=False):
    with db_cursor() as cur:
        categories = [dict(r) for r in cur.execute(
            "SELECT * FROM categories ORDER BY sort_order, name"
        ).fetchall()]
        if include_fields:
            for cat in categories:
                cat["fields"] = list_fields(cat["id"])
    return categories


def get_category(category_id):
    with db_cursor() as cur:
        row = cur.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
        return dict(row) if row else None


def list_fields(category_id):
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM category_fields WHERE category_id = ? ORDER BY sort_order, id",
            (category_id,),
        ).fetchall()
    fields = []
    for r in rows:
        f = dict(r)
        f["field_options"] = json.loads(f["field_options"]) if f["field_options"] else None
        fields.append(f)
    return fields


def create_category(name, fields):
    """fields: list of dicts with field_key, field_label, field_type, field_options, is_required"""
    slug = slugify(name)
    with db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO categories (name, slug, is_builtin, sort_order) VALUES (?, ?, 0, "
            "(SELECT COALESCE(MAX(sort_order), 0) + 1 FROM categories))",
            (name, slug),
        )
        category_id = cur.lastrowid
        for order, f in enumerate(fields):
            cur.execute(
                """INSERT INTO category_fields
                   (category_id, field_key, field_label, field_type, field_options, is_required, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (category_id, slugify(f["field_key"]).replace("-", "_"), f["field_label"],
                 f.get("field_type", "text"),
                 json.dumps(f["field_options"]) if f.get("field_options") else None,
                 int(f.get("is_required", False)), order),
            )
    return category_id


def add_field(category_id, field_label, field_type="text", field_options=None, is_required=False):
    field_key = slugify(field_label).replace("-", "_")
    with db_cursor(commit=True) as cur:
        existing = cur.execute(
            "SELECT id FROM category_fields WHERE category_id = ? AND field_key = ?",
            (category_id, field_key),
        ).fetchone()
        if existing:
            raise ValueError(f'يوجد بالفعل حقل بنفس الاسم "{field_label}" في هذا القسم.')
        next_order = cur.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM category_fields WHERE category_id = ?",
            (category_id,),
        ).fetchone()[0]
        cur.execute(
            """INSERT INTO category_fields
               (category_id, field_key, field_label, field_type, field_options, is_required, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (category_id, field_key, field_label, field_type,
             json.dumps(field_options) if field_options else None, int(is_required), next_order),
        )
        return cur.lastrowid


def update_field(field_id, field_label, field_type="text", field_options=None, is_required=False):
    field_key = slugify(field_label).replace("-", "_")
    with db_cursor(commit=True) as cur:
        row = cur.execute("SELECT * FROM category_fields WHERE id = ?", (field_id,)).fetchone()
        if not row:
            raise ValueError("Field not found.")
        existing = cur.execute(
            "SELECT id FROM category_fields WHERE category_id = ? AND field_key = ? AND id != ?",
            (row["category_id"], field_key, field_id),
        ).fetchone()
        if existing:
            raise ValueError(f'يوجد بالفعل حقل بنفس الاسم "{field_label}" في هذا القسم.')
        cur.execute(
            "UPDATE category_fields SET field_key = ?, field_label = ?, field_type = ?, field_options = ?, is_required = ? WHERE id = ?",
            (field_key, field_label, field_type,
             json.dumps(field_options) if field_options else None, int(is_required), field_id),
        )


def delete_field(field_id):
    with db_cursor(commit=True) as cur:
        row = cur.execute("SELECT id FROM category_fields WHERE id = ?", (field_id,)).fetchone()
        if not row:
            raise ValueError("Field not found.")
        cur.execute("DELETE FROM category_fields WHERE id = ?", (field_id,))


def delete_category(category_id):
    with db_cursor(commit=True) as cur:
        row = cur.execute("SELECT id FROM categories WHERE id = ?", (category_id,)).fetchone()
        if not row:
            raise ValueError("Category not found.")
        product_count = cur.execute(
            "SELECT COUNT(*) FROM products WHERE category_id = ?", (category_id,)
        ).fetchone()[0]
        if product_count > 0:
            raise ValueError("لا يمكن حذف القسم أثناء وجود منتجات مرتبطة به. احذف المنتجات أو غيّر قسمها أولاً.")
        cur.execute("DELETE FROM categories WHERE id = ?", (category_id,))
