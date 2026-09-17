from flask import Blueprint, render_template
from app.services import product_audit

bp = Blueprint("branding", __name__)


@bp.route("/about")
def about():
    return render_template(
        "about.html",
        today_changes=product_audit.today_events(),
        product_event_labels=product_audit.EVENT_LABELS_AR,
    )
