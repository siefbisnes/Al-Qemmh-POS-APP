import os

from flask import Blueprint, current_app, send_from_directory

bp = Blueprint("media", __name__, url_prefix="/media")


@bp.route("/products/<filename>")
def product_image(filename):
    return send_from_directory(current_app.config["PRODUCT_IMAGES_DIR"], filename)
