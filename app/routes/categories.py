from flask import Blueprint, render_template, request, redirect, url_for, flash

from app.services import categories as category_service

bp = Blueprint("categories", __name__, url_prefix="/categories")


@bp.route("/")
def index():
    categories = category_service.list_categories(include_fields=True)
    return render_template("categories.html", categories=categories)


@bp.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Category name is required.", "error")
            return render_template("category_form.html")

        labels = request.form.getlist("field_label")
        types = request.form.getlist("field_type")
        options_list = request.form.getlist("field_options")
        required_flags = request.form.getlist("field_required")  # values present only for checked boxes

        # required_flags comes as a list of the *indices* we mark required via hidden inputs - see template
        fields = []
        for i, label in enumerate(labels):
            label = label.strip()
            if not label:
                continue
            field_type = types[i] if i < len(types) else "text"
            options = None
            if field_type == "select" and i < len(options_list):
                options = [opt.strip() for opt in options_list[i].split(",") if opt.strip()]
            fields.append({
                "field_key": label,
                "field_label": label,
                "field_type": field_type,
                "field_options": options,
                "is_required": str(i) in required_flags,
            })

        category_service.create_category(name, fields)
        flash(f'Category "{name}" created with {len(fields)} field(s).', "success")
        return redirect(url_for("categories.index"))

    return render_template("category_form.html")


@bp.route("/<int:category_id>/fields/add", methods=["POST"])
def add_field(category_id):
    label = request.form.get("field_label", "").strip()
    field_type = request.form.get("field_type", "text")
    field_options = request.form.get("field_options", "").strip()
    is_required = request.form.get("is_required") == "on"
    if label:
        options = [opt.strip() for opt in field_options.split(",") if opt.strip()] if field_type == "select" else None
        try:
            category_service.add_field(category_id, label, field_type, field_options=options, is_required=is_required)
            flash(f'Field "{label}" added.', "success")
        except ValueError as e:
            flash(str(e), "error")
    return redirect(url_for("categories.index"))


@bp.route("/<int:category_id>/fields/<int:field_id>/update", methods=["POST"])
def update_field(category_id, field_id):
    label = request.form.get("field_label", "").strip()
    field_type = request.form.get("field_type", "text")
    field_options = request.form.get("field_options", "").strip()
    is_required = request.form.get("is_required") == "on"
    options = [opt.strip() for opt in field_options.split(",") if opt.strip()] if field_type == "select" else None
    try:
        category_service.update_field(field_id, label, field_type, field_options=options, is_required=is_required)
        flash(f'Field "{label}" updated.', "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("categories.index"))


@bp.route("/<int:category_id>/fields/<int:field_id>/delete", methods=["POST"])
def delete_field(category_id, field_id):
    try:
        category_service.delete_field(field_id)
        flash("Field removed.", "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("categories.index"))


@bp.route("/<int:category_id>/delete", methods=["POST"])
def delete(category_id):
    try:
        category_service.delete_category(category_id)
        flash("Category removed.", "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("categories.index"))
