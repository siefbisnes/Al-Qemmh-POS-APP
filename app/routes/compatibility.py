from flask import Blueprint, render_template, request

from app.services import compatibility as compatibility_service

bp = Blueprint("compatibility", __name__, url_prefix="/compatibility")


@bp.route("/")
def search():
    term = request.args.get("q", "").strip()
    results = compatibility_service.search(term) if term else []
    return render_template("compatibility_search.html", term=term, results=results)
