from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, session, send_file

from app.routes.notifications import notify_event
from app.services import products as product_service
from app.services import categories as category_service
from app.services import compatibility as compatibility_service

bp = Blueprint("products", __name__, url_prefix="/products")


@bp.route("/<int:product_id>")
def detail(product_id):
    product = product_service.get_product(product_id)
    if not product:
        abort(404)
    return render_template("product_detail.html", product=product)


@bp.route("/new", methods=["GET", "POST"])
def new():
    categories = category_service.list_categories(include_fields=True)

    if request.method == "POST":
        category_id = request.form.get("category_id", type=int)
        if not category_id:
            flash("Please choose a category.", "error")
            return render_template("product_form.html", categories=categories, product=None,
                                    form=request.form)

        spec_values = _collect_spec_values(category_id, categories)

        purchase_price = request.form.get("purchase_price", type=float) or 0

        product_id = product_service.create_product(
            category_id=category_id,
            name=request.form.get("name", "").strip(),
            grade=request.form.get("grade", "A").strip() or "A",
            quantity=request.form.get("quantity", type=int) or 0,
            description=request.form.get("description", "").strip(),
            purchase_price=purchase_price,
            selling_price=request.form.get("selling_price", type=float) or 0,
            spec_values=spec_values,
        )

        _handle_image_uploads(product_id)

        title = "Product Added"
        message = f"تم إضافة {request.form.get('name', '').strip() or 'منتج'} بنجاح."
        notify_event("product_added", title, message, url=url_for("products.detail", product_id=product_id))

        flash("Product added.", "success")
        return redirect(url_for("products.detail", product_id=product_id))

    return render_template("product_form.html", categories=categories, product=None, form=None)


@bp.route("/<int:product_id>/edit", methods=["GET", "POST"])
def edit(product_id):
    product = product_service.get_product(product_id)
    if not product:
        abort(404)
    categories = category_service.list_categories(include_fields=True)

    if request.method == "POST":
        category_id = request.form.get("category_id", type=int)
        spec_values = _collect_spec_values(category_id, categories)

        purchase_price = product["purchase_price"]
        submitted_purchase_price = request.form.get("purchase_price", type=float)
        if session.get("role") == "admin":
            purchase_price = submitted_purchase_price or 0
        elif submitted_purchase_price is not None:
            purchase_price = submitted_purchase_price

        updated_name = request.form.get("name", "").strip()
        updated_quantity = request.form.get("quantity", type=int) or 0
        updated_price = request.form.get("selling_price", type=float) or 0
        product_service.update_product(
            product_id=product_id,
            category_id=category_id,
            name=updated_name,
            grade=request.form.get("grade", "A").strip() or "A",
            quantity=updated_quantity,
            description=request.form.get("description", "").strip(),
            purchase_price=purchase_price,
            selling_price=updated_price,
            spec_values=spec_values,
        )
        _handle_image_uploads(product_id)

        # Notify ONLY on the two changes that should ever alert someone:
        # selling price and quantity. A name-only edit or a purchase-price
        # change (سعر الشراء - cost basis, not customer-facing) must NOT
        # notify - purchase price is deliberately excluded here, same as
        # it always has been; the name check that used to also trigger
        # this has been removed since it's not one of the two allowed
        # triggers.
        if (
            updated_quantity != int(product.get("quantity") or 0)
            or updated_price != float(product.get("selling_price") or 0)
        ):
            title = "Product Updated"
            message = f"تم تحديث {updated_name or product['name']} — الكمية: {updated_quantity} — السعر: {updated_price:.2f} ج.م."
            notify_event("product_updated", title, message, url=url_for("products.detail", product_id=product_id))
        flash("Product updated.", "success")
        return redirect(url_for("products.detail", product_id=product_id))

    current_specs = {}
    with_fields = next((c for c in categories if c["id"] == product["category_id"]), None)
    if with_fields:
        by_label = {s["field_label"]: s["value"] for s in product["specifications"]}
        for f in with_fields["fields"]:
            if f["field_label"] in by_label:
                current_specs[f["id"]] = by_label[f["field_label"]]

    return render_template("product_form.html", categories=categories, product=product,
                            current_specs=current_specs, form=None)


@bp.route("/<int:product_id>/restore", methods=["POST"])
def restore(product_id):
    quantity = request.form.get("quantity", type=int) or 1
    product_service.restore_product(product_id, quantity)
    product = product_service.get_product(product_id)
    product_name = product["name"] if product else "منتج"
    title = "Product Restored"
    message = f"تم إرجاع {quantity} وحدة إلى المخزون — {product_name}."
    notify_event("product_restored", title, message,
                  url=url_for("products.detail", product_id=product_id))
    flash("Product restored to active inventory.", "success")
    return redirect(url_for("dashboard.index"))


@bp.route("/<int:product_id>/delete", methods=["POST"])
def delete(product_id):
    from flask import jsonify

    product = product_service.get_product(product_id)
    if not product:
        if request.headers.get("Accept") == "application/json" or request.is_json:
            return jsonify({"success": False, "message": "المنتج غير موجود."}), 404
        abort(404)

    wants_json = request.headers.get("Accept") == "application/json" or request.is_json
    try:
        product_service.soft_delete_product(product_id)
    except Exception as e:
        if wants_json:
            return jsonify({"success": False, "message": str(e)}), 500
        flash("حصل خطأ أثناء حذف المنتج.", "error")
        return redirect(url_for("products.detail", product_id=product_id))

    if wants_json:
        return jsonify({"success": True, "product_id": product_id})

    flash("Product removed from inventory.", "success")
    return redirect(url_for("dashboard.index"))


@bp.route("/<int:product_id>/writeoff", methods=["POST"])
def writeoff(product_id):
    """هالك: logs cost + forgone revenue, drops stock, and feeds the
    owner dashboard Net Profit + damaged charts."""
    from app.services import writeoffs as writeoff_service

    product = product_service.get_product(product_id)
    if not product:
        abort(404)
    quantity = request.form.get("quantity", type=int)
    note = request.form.get("note", "").strip()
    try:
        result = writeoff_service.create_writeoff(product_id, quantity, note=note)
        flash(
            f"تم تسجيل {result['quantity']} وحدة هالك — "
            f"خسارة تكلفة {result['cost_loss']:.2f} ج.م · "
            f"قيمة بيع ضائعة {result['revenue_loss']:.2f} ج.م.",
            "success",
        )
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("products.detail", product_id=product_id))


@bp.route("/<int:product_id>/remove-stock", methods=["POST"])
def remove_stock(product_id):
    """مرتجع on the product detail page: a partial, manual stock decrease
    (damaged unit, personal use, correcting a miscount, etc.) - NOT a
    sale, and NOT the same as the full soft-delete above. Only touches
    products.quantity, so sales/purchases/expenses and every report stay
    exactly as they were."""
    product = product_service.get_product(product_id)
    if not product:
        abort(404)
    quantity = request.form.get("quantity", type=int)
    try:
        product_service.remove_stock(product_id, quantity)
        flash(f"تم مرتجع {quantity} وحدة من المخزون.", "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("products.detail", product_id=product_id))


@bp.route("/<int:product_id>/images/upload", methods=["POST"])
def upload_images(product_id):
    product = product_service.get_product(product_id)
    if not product:
        abort(404)
    _handle_image_uploads(product_id)
    flash("Photos uploaded.", "success")
    return redirect(url_for("products.detail", product_id=product_id))


@bp.route("/<int:product_id>/images/<int:image_id>/delete", methods=["POST"])
def delete_image(product_id, image_id):
    product = product_service.get_product(product_id)
    if not product:
        abort(404)
    res = product_service.delete_product_image(image_id)
    if not res:
        flash("الصورة غير موجودة.", "error")
        return redirect(url_for("products.detail", product_id=product_id))
    flash("تم حذف الصورة.", "success")
    from flask import request, jsonify
    if request.headers.get("Accept") == "application/json" or request.is_json:
        return jsonify({"success": True, "image_id": image_id})
    return redirect(url_for("products.detail", product_id=product_id))


@bp.route("/<int:product_id>/compatibility/add", methods=["POST"])
def add_compatibility(product_id):
    component_type = request.form.get("component_type", "").strip()
    component_value = request.form.get("component_value", "").strip()
    if component_type and component_value:
        compatibility_service.add_entry(product_id, component_type, component_value,
                                         request.form.get("notes", "").strip() or None)
        flash("Compatibility entry added.", "success")
    return redirect(url_for("products.detail", product_id=product_id))


@bp.route("/<int:product_id>/compatibility/<int:entry_id>/delete", methods=["POST"])
def delete_compatibility(product_id, entry_id):
    compatibility_service.delete_entry(entry_id)
    flash("Compatibility entry removed.", "success")
    return redirect(url_for("products.detail", product_id=product_id))


@bp.route("/<int:product_id>/download")
def download_package(product_id):
    """Browser-mode counterpart to the desktop app's 'تحميل' button
    (window.pywebview.api.export_product_package in product_detail.html,
    implemented in run.py's AppAPI). pywebview's WebView has no real
    browser download manager, so that path saves straight to disk via a
    native Save dialog instead of an HTTP response - this route is only
    reached when the page is opened in a normal browser (no pywebview
    bridge available), where a plain Flask attachment download works
    fine and is exactly what a browser expects.

    Both paths call products.build_product_package_zip() so the zip
    contents (images + specs.txt) can never drift between the two
    environments - only how the bytes get to the user differs."""
    import io

    result = product_service.build_product_package_zip(product_id)
    if not result:
        abort(404)
    safe_name, content = result
    return send_file(
        io.BytesIO(content),
        as_attachment=True,
        download_name=f"{safe_name}.zip",
        mimetype="application/zip",
    )


# ---------- helpers ----------

def _collect_spec_values(category_id, categories):
    category = next((c for c in categories if c["id"] == category_id), None)
    spec_values = {}
    if category:
        for f in category["fields"]:
            value = request.form.get(f"spec_{f['id']}", "").strip()
            if value:
                spec_values[f["id"]] = value
    return spec_values


def _handle_image_uploads(product_id):
    files = [f for f in request.files.getlist("images") if f and f.filename]
    valid_files = [f for f in files if product_service.allowed_image(f.filename)]
    if len(valid_files) < len(files):
        flash("Some files were skipped (only jpg/png/webp images are accepted).", "error")
    if valid_files:
        product_service.save_uploaded_images(valid_files, product_id=product_id)