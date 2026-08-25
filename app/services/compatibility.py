from app.db import db_cursor


def list_for_product(product_id):
    with db_cursor() as cur:
        return [dict(r) for r in cur.execute(
            "SELECT * FROM compatibility WHERE product_id = ? ORDER BY component_type, component_value",
            (product_id,),
        ).fetchall()]


def add_entry(product_id, component_type, component_value, notes=None):
    with db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO compatibility (product_id, component_type, component_value, notes) VALUES (?, ?, ?, ?)",
            (product_id, component_type.strip(), component_value.strip(), notes),
        )
        return cur.lastrowid


def delete_entry(entry_id):
    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM compatibility WHERE id = ?", (entry_id,))


def search(term):
    """Search by component value (e.g. 'E5-1650 V3') and return the host
    products it's compatible with. Never guesses - only returns rows the
    store owner actually entered."""
    with db_cursor() as cur:
        rows = cur.execute(
            """SELECT comp.*, p.name AS product_name, p.id AS product_id, p.grade, p.quantity
               FROM compatibility comp
               JOIN products p ON p.id = comp.product_id AND p.is_active = 1
               WHERE comp.component_value LIKE ? OR comp.component_type LIKE ?
               ORDER BY p.name""",
            (f"%{term}%", f"%{term}%"),
        ).fetchall()
    return [dict(r) for r in rows]
