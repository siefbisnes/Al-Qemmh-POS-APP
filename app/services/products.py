import os
import re
import uuid
from datetime import date, datetime, timedelta

from flask import current_app
from PIL import Image

from app.db import db_cursor


# ---------- queries ----------

def is_stagnant_product(product, cutoff=None):
    """Return True when the current stock appears to have been unsold for 60+ days.

    The comparison is anchored to the last sale date when one exists; otherwise,
    it falls back to the product's creation date so products that were added long
    ago and never sold are still treated as stagnant.
    """
    if not product:
        return False
    if int(product.get("quantity") or 0) <= 0:
        return False
    if int(product.get("is_active", 1) or 0) != 1:
        return False
    if str(product.get("source") or "") == "service_placeholder":
        return False

    if cutoff is None:
        cutoff = date.today() - timedelta(days=60)
    elif isinstance(cutoff, str):
        try:
            cutoff = date.fromisoformat(cutoff[:10])
        except ValueError:
            return False

    last_sale = _coerce_date(product.get("last_sale"))
    date_added = _coerce_date(product.get("date_added"))
    anchor = last_sale or date_added
    return bool(anchor is not None and anchor <= cutoff)


def _coerce_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def list_products(search=None, category_id=None, grade=None, low_stock_only=False, include_inactive=False,
                   include_sold=False, stagnant_60_days=False):
    query = """
        SELECT p.*, c.name AS category_name, c.slug AS category_slug,
               (SELECT filepath FROM product_images pi
                WHERE pi.product_id = p.id ORDER BY pi.is_primary DESC, pi.sort_order LIMIT 1) AS thumbnail
        FROM products p
        JOIN categories c ON c.id = p.category_id
        WHERE 1 = 1
    """
    params = []
    if not include_inactive:
        query += " AND p.is_active = 1"
    if not include_sold:
        query += " AND p.quantity > 0"
    if search:
        query += " AND p.name LIKE ?"
        params.append(f"%{search}%")
    if category_id:
        query += " AND p.category_id = ?"
        params.append(category_id)
    if grade:
        query += " AND p.grade = ?"
        params.append(grade)
    if low_stock_only:
        from app.services import settings as settings_service
        query += " AND p.quantity <= ?"
        params.append(settings_service.low_stock_threshold())
    query += " ORDER BY p.date_added DESC"

    with db_cursor() as cur:
        rows = cur.execute(query, params).fetchall()

    products = [dict(r) for r in rows]
    if stagnant_60_days:
        cutoff = date.today() - timedelta(days=60)
        products = [p for p in products if is_stagnant_product(p, cutoff=cutoff)]
    return products


def list_sold_products(search=None, category_id=None):
    """Products that reached zero quantity - shown in their own gray "SOLD"
    section instead of mixed in with active, sellable inventory."""
    query = """
        SELECT p.*, c.name AS category_name, c.slug AS category_slug,
               (SELECT filepath FROM product_images pi
                WHERE pi.product_id = p.id ORDER BY pi.is_primary DESC, pi.sort_order LIMIT 1) AS thumbnail
        FROM products p
        JOIN categories c ON c.id = p.category_id
        WHERE p.is_active = 1 AND p.quantity <= 0
    """
    params = []
    if search:
        query += " AND p.name LIKE ?"
        params.append(f"%{search}%")
    if category_id:
        query += " AND p.category_id = ?"
        params.append(category_id)
    query += " ORDER BY p.date_added DESC"

    with db_cursor() as cur:
        rows = cur.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def restore_product(product_id, quantity=1):
    """Bring a sold-out product back into active inventory with new stock."""
    quantity = max(1, int(quantity))
    with db_cursor(commit=True) as cur:
        cur.execute("UPDATE products SET quantity = quantity + ? WHERE id = ?", (quantity, product_id))


def get_product(product_id):
    with db_cursor() as cur:
        row = cur.execute(
            """SELECT p.*, c.name AS category_name, c.slug AS category_slug
               FROM products p JOIN categories c ON c.id = p.category_id
               WHERE p.id = ?""",
            (product_id,),
        ).fetchone()
        if not row:
            return None
        product = dict(row)
        product["images"] = [dict(r) for r in cur.execute(
            "SELECT * FROM product_images WHERE product_id = ? ORDER BY is_primary DESC, sort_order",
            (product_id,),
        ).fetchall()]
        spec_rows = cur.execute(
            """SELECT cf.field_key, cf.field_label, cf.field_type, s.value
               FROM specifications s JOIN category_fields cf ON cf.id = s.category_field_id
               WHERE s.product_id = ? ORDER BY cf.sort_order""",
            (product_id,),
        ).fetchall()
        product["specifications"] = [dict(r) for r in spec_rows]
        product["compatibility"] = [dict(r) for r in cur.execute(
            "SELECT * FROM compatibility WHERE product_id = ? ORDER BY component_type, component_value",
            (product_id,),
        ).fetchall()]
        product["sales_history"] = [dict(r) for r in cur.execute(
            "SELECT * FROM sales WHERE product_id = ? AND is_voided = 0 ORDER BY sale_date DESC", (product_id,),
        ).fetchall()]
    from app.services import writeoffs as writeoff_service
    product["writeoffs"] = writeoff_service.list_for_product(product_id)
    return product


def get_service_placeholder_product():
    with db_cursor() as cur:
        row = cur.execute(
            "SELECT id FROM products WHERE source = ? LIMIT 1",
            ("service_placeholder",),
        ).fetchone()
        if row:
            return row["id"]
    with db_cursor(commit=True) as cur:
        category = cur.execute("SELECT id FROM categories ORDER BY id LIMIT 1").fetchone()
        if category:
            category_id = category["id"]
        else:
            cur.execute(
                "INSERT INTO categories (name, slug, is_builtin, sort_order) VALUES (?, ?, 1, 9999)",
                ("خدمات", "services"),
            )
            category_id = cur.lastrowid
        cur.execute(
            """INSERT INTO products
               (category_id, name, grade, quantity, description, purchase_price,
                selling_price, date_added, is_active, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                category_id,
                "خدمة مخصصة",
                "A",
                0,
                "Service placeholder",
                0,
                0,
                datetime.now().isoformat(timespec="seconds"),
                0,
                "service_placeholder",
            ),
        )
        return cur.lastrowid

# ---------- create / update ----------

def create_product(category_id, name, grade, quantity, description,
                    purchase_price, selling_price, spec_values, source="manual"):
    """spec_values: dict of {category_field_id: value}"""
    _validate_product_values(quantity, purchase_price, selling_price)
    with db_cursor(commit=True) as cur:
        cur.execute(
            """INSERT INTO products
               (category_id, name, grade, quantity, description, purchase_price, selling_price, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (category_id, name, grade, quantity, description, purchase_price, selling_price, source),
        )
        product_id = cur.lastrowid
        _save_specifications(cur, product_id, spec_values)
    return product_id


def update_product(product_id, category_id, name, grade, quantity, description,
                    purchase_price, selling_price, spec_values):
    _validate_product_values(quantity, purchase_price, selling_price)
    with db_cursor(commit=True) as cur:
        cur.execute(
            """UPDATE products SET category_id = ?, name = ?, grade = ?, quantity = ?, description = ?,
               purchase_price = ?, selling_price = ? WHERE id = ?""",
            (category_id, name, grade, quantity, description, purchase_price, selling_price, product_id),
        )
        cur.execute("DELETE FROM specifications WHERE product_id = ?", (product_id,))
        _save_specifications(cur, product_id, spec_values)


def _save_specifications(cur, product_id, spec_values):
    for field_id, value in (spec_values or {}).items():
        if value in (None, ""):
            continue
        cur.execute(
            "INSERT INTO specifications (product_id, category_field_id, value) VALUES (?, ?, ?)",
            (product_id, field_id, value),
        )


def soft_delete_product(product_id):
    with db_cursor(commit=True) as cur:
        cur.execute("UPDATE products SET is_active = 0 WHERE id = ?", (product_id,))


def _validate_product_values(quantity, purchase_price, selling_price):
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ValueError("الكمية يجب أن تكون رقمًا صحيحًا.")
    if quantity < 0:
        raise ValueError("الكمية لا يمكن أن تكون سالبة.")

    for label, value in (("سعر الشراء", purchase_price), ("سعر البيع", selling_price)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{label} يجب أن يكون رقمًا صحيحًا.")
        if value < 0:
            raise ValueError(f"{label} لا يمكن أن يكون سالبًا.")


def adjust_quantity(product_id, delta, cur=None):
    """delta can be negative (sale) or positive (restock)."""
    try:
        delta = int(delta)
    except (TypeError, ValueError):
        raise ValueError("الكمية يجب أن تكون رقمًا صحيحًا.")
    if delta == 0:
        return

    def _run(c):
        c.execute("UPDATE products SET quantity = quantity + ? WHERE id = ?", (delta, product_id))

    if cur is not None:
        _run(cur)
    else:
        with db_cursor(commit=True) as c:
            _run(c)


def remove_stock(product_id, quantity):
    """Manual partial removal from inventory - damaged units, personal
    use, correcting a miscount, etc. Just decrements products.quantity;
    it is NOT a sale (no sales/transactions row, no customer, no
    payment) and NOT the same as soft_delete_product (which hides the
    whole product regardless of quantity). Because it never touches
    sales, purchases, or expenses, none of the reports are affected -
    they're built entirely from those tables, not from product stock
    counts."""
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ValueError("الكمية يجب أن تكون رقمًا صحيحًا.")
    if quantity <= 0:
        raise ValueError("الكمية يجب أن تكون أكبر من صفر.")

    with db_cursor(commit=True) as cur:
        row = cur.execute("SELECT quantity FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise ValueError("المنتج غير موجود.")
        if quantity > row["quantity"]:
            raise ValueError(f"لا يمكن مرتجع {quantity} وحدة، الموجود بالمخزون {row['quantity']} فقط.")
        cur.execute("UPDATE products SET quantity = quantity - ? WHERE id = ?", (quantity, product_id))


# ---------- images ----------

def save_uploaded_images(files, product_id=None):
    """files: list of werkzeug FileStorage objects (already validated by the route).
    Every upload is converted to WebP (smaller files -> faster catalog/product
    page loads). If conversion fails for a given file (corrupt/unsupported
    image), that file falls back to being saved in its original format
    rather than losing the upload."""
    saved = []
    target_dir = current_app.config["PRODUCT_IMAGES_DIR"]
    product_name = None
    if product_id:
        product = get_product(product_id)
        product_name = product["name"] if product else None

    with db_cursor(commit=True) as cur:
        existing_count = 0
        if product_id:
            existing_count = cur.execute(
                "SELECT COUNT(*) FROM product_images WHERE product_id = ?", (product_id,)
            ).fetchone()[0]
        for i, file in enumerate(files):
            orig_ext = file.filename.rsplit(".", 1)[-1].lower()
            if product_name:
                filename = build_product_image_filename(product_name, product_id, existing_count + i, ".webp")
            else:
                filename = f"{uuid.uuid4().hex}.webp"
            filename = _unique_filename(target_dir, filename)
            full_path = os.path.join(target_dir, filename)

            tmp_path = full_path + f".upload.{orig_ext}"
            file.save(tmp_path)

            if _convert_to_webp(tmp_path, full_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            else:
                # Conversion failed - keep the original bytes under their
                # original extension instead of losing the upload.
                fallback_name = _unique_filename(target_dir, f"{os.path.splitext(filename)[0]}.{orig_ext}")
                fallback_path = os.path.join(target_dir, fallback_name)
                os.replace(tmp_path, fallback_path)
                filename = fallback_name
                full_path = fallback_path
                _make_thumbnail_safe(full_path)

            is_primary = 1 if (existing_count == 0 and i == 0) else 0
            cur.execute(
                "INSERT INTO product_images (product_id, filepath, is_primary, sort_order) VALUES (?, ?, ?, ?)",
                (product_id, filename, is_primary, existing_count + i),
            )
            saved.append(filename)
    return saved


def _convert_to_webp(src_path, dst_path, quality=82, max_size=1600):
    """Convert src_path to a resized WebP written to dst_path. Returns True
    on success. Never raises and never touches src_path - a bad/corrupt
    upload just fails this conversion, it never crashes the request or
    loses the original file."""
    try:
        with Image.open(src_path) as img:
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")
            if max(img.size) > max_size:
                img.thumbnail((max_size, max_size))
            img.save(dst_path, "WEBP", quality=quality, method=6)
        return True
    except Exception:
        if os.path.exists(dst_path):
            try:
                os.remove(dst_path)
            except OSError:
                pass
        return False


def _make_thumbnail_safe(full_path, max_size=1600):
    """Downscale very large photos so the catalog stays fast on an old shop PC.
    Never raises - a thumbnail failure should never block saving the product."""
    try:
        with Image.open(full_path) as img:
            if max(img.size) > max_size:
                img.thumbnail((max_size, max_size))
                img.save(full_path)
    except Exception:
        pass


def _slugify_product_name(name):
    name = (name or "").strip()
    slug = re.sub(r"[^\w]+", "-", name, flags=re.UNICODE).strip("-")
    slug = slug.lower()
    return slug or uuid.uuid4().hex


def _unique_filename(target_dir, filename):
    candidate = filename
    base, ext = os.path.splitext(filename)
    counter = 1
    while os.path.exists(os.path.join(target_dir, candidate)):
        candidate = f"{base}-{counter}{ext}"
        counter += 1
    return candidate


def build_product_image_filename(product_name, product_id, index, ext):
    slug = _slugify_product_name(product_name)
    return f"{slug}-{product_id}-{index + 1}{ext}"


def delete_product_image(image_id):
    """Delete a product image row and its file from disk. Returns (product_id, filepath, was_primary) or None if not found."""
    with db_cursor(commit=True) as cur:
        row = cur.execute("SELECT id, product_id, filepath, is_primary FROM product_images WHERE id = ?", (image_id,)).fetchone()
        if not row:
            return None
        product_id = row["product_id"]
        filepath = row["filepath"]
        was_primary = bool(row["is_primary"])
        # delete DB row
        cur.execute("DELETE FROM product_images WHERE id = ?", (image_id,))
        # if it was primary, promote another image to primary
        if was_primary:
            next_row = cur.execute(
                "SELECT id FROM product_images WHERE product_id = ? ORDER BY sort_order LIMIT 1",
                (product_id,),
            ).fetchone()
            if next_row:
                cur.execute("UPDATE product_images SET is_primary = 1 WHERE id = ?", (next_row[0],))
    # remove file from disk (best-effort)
    try:
        full_path = os.path.join(current_app.config["PRODUCT_IMAGES_DIR"], filepath)
        if os.path.exists(full_path):
            os.remove(full_path)
    except Exception:
        pass
    return (product_id, filepath, was_primary)


def rename_product_images(product_id, product_name=None):
    """Rename all product images on disk and update DB filepaths to match the product model."""
    if product_name is None:
        product = get_product(product_id)
        if not product:
            raise ValueError("Product not found")
        product_name = product["name"]

    target_dir = current_app.config["PRODUCT_IMAGES_DIR"]
    renamed = []
    with db_cursor(commit=True) as cur:
        rows = cur.execute(
            "SELECT id, filepath FROM product_images WHERE product_id = ? ORDER BY is_primary DESC, sort_order",
            (product_id,),
        ).fetchall()

        for i, row in enumerate(rows):
            old_filepath = row["filepath"]
            _, ext = os.path.splitext(old_filepath)
            new_filename = build_product_image_filename(product_name, product_id, i, ext)
            new_filename = _unique_filename(target_dir, new_filename)
            old_full = os.path.join(target_dir, old_filepath)
            new_full = os.path.join(target_dir, new_filename)
            if os.path.exists(old_full) and old_filepath != new_filename:
                try:
                    os.rename(old_full, new_full)
                except OSError:
                    continue
            cur.execute(
                "UPDATE product_images SET filepath = ? WHERE id = ?",
                (new_filename, row["id"]),
            )
            renamed.append(new_filename)
    return renamed


def allowed_image(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]


# ---------- product package download (specs + images as .zip) ----------
#
# Single source of truth for "تحميل" (Download) on the product detail page.
# Builds the same zip bytes regardless of caller, so the desktop pywebview
# save-dialog path (run.py AppAPI.export_product_package) and the browser
# HTTP download route (app/routes/products.py, added for browser support)
# can never disagree about what gets packaged. Only the *delivery*
# mechanism differs between the two environments - this function only
# ever produces bytes, it never touches a save dialog or an HTTP response.

def safe_product_filename(product_name, product_id):
    """Filesystem/zip-entry-safe name for a product, e.g. 'product-12' if
    the name is empty or entirely made of unsafe characters."""
    safe_name = "".join(c for c in (product_name or "") if c.isalnum() or c in " _-").strip()
    return safe_name or f"product-{product_id}"


def build_product_package_zip(product_id):
    """Returns (safe_filename_without_ext, zip_bytes) or None if the
    product doesn't exist. Missing image files on disk are skipped
    rather than raising, so a stale product_images row can never break
    the whole download."""
    import io
    import zipfile

    product = get_product(product_id)
    if not product:
        return None

    images_dir = current_app.config["PRODUCT_IMAGES_DIR"]
    safe_name = safe_product_filename(product["name"], product_id)

    spec_lines = [f"الموديل: {product['name']}"]
    for s in product.get("specifications") or []:
        spec_lines.append(f"{s['field_label']}: {s['value']}")
    if product.get("description"):
        spec_lines.append("")
        spec_lines.append(f"الوصف: {product['description']}")
    specs_txt = "\n".join(spec_lines)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{safe_name}/specs.txt", specs_txt)
        for img in product.get("images") or []:
            filepath = img["filepath"]
            full_path = os.path.join(images_dir, filepath)
            if os.path.exists(full_path):
                zf.write(full_path, f"{safe_name}/images/{filepath}")

    return safe_name, buf.getvalue()
