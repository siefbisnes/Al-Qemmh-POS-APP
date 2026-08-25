from flask import Blueprint, render_template, request

from app.services import products as product_service
from app.services import categories as category_service

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def index():
    search = request.args.get("q", "").strip() or None
    category_id = request.args.get("category", type=int)
    grade = request.args.get("grade", "").strip() or None
    low_stock_only = request.args.get("low_stock") == "1"
    stagnant_60_days = request.args.get("stagnant_60_days") == "1"

    items = product_service.list_products(
        search=search,
        category_id=category_id,
        grade=grade,
        low_stock_only=low_stock_only,
        stagnant_60_days=stagnant_60_days,
    )
    sold_items = product_service.list_sold_products(search=search, category_id=category_id)
    categories = category_service.list_categories()

    return render_template(
        "dashboard.html",
        products=items,
        sold_products=sold_items,
        categories=categories,
        filters={"q": search or "", "category": category_id, "grade": grade or "",
                 "low_stock": low_stock_only, "stagnant_60_days": stagnant_60_days},
    )
