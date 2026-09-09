import os

import hmac

from flask import Blueprint, jsonify, render_template, request, send_file, abort, current_app

from app.services import quran as quran_service

bp = Blueprint("quran", __name__, url_prefix="/quran")


@bp.before_request
def require_desktop_access():
    """Keep Quran routes available only inside the native desktop window."""
    expected_token = current_app.config.get("DESKTOP_QURAN_TOKEN")
    request_token = request.args.get("desktop_token")
    cookie_token = request.cookies.get("alqemma_desktop_quran")

    if not expected_token or not (
        hmac.compare_digest(request_token or "", expected_token)
        or hmac.compare_digest(cookie_token or "", expected_token)
    ):
        abort(404)


@bp.route("/player")
def player():
    reciters = quran_service.get_reciters()
    settings = quran_service.get_settings()
    return render_template("quran_player.html", reciters=reciters, quran_settings=settings)


@bp.route("/api/library")
def api_library():
    reciters = quran_service.get_reciters()
    return jsonify({
        "reciters": [
            {"id": r["id"], "name": r["name"], "track_count": r["track_count"]}
            for r in reciters
        ],
    })


@bp.route("/api/reciters/<reciter_id>")
def api_reciter(reciter_id):
    reciter = quran_service.get_reciter(reciter_id)
    if not reciter:
        return jsonify({"error": "الشيخ غير موجود"}), 404
    return jsonify(reciter)


@bp.route("/api/rescan", methods=["POST"])
def api_rescan():
    reciters = quran_service.rescan()
    return jsonify({
        "reciters": [
            {"id": r["id"], "name": r["name"], "track_count": r["track_count"]}
            for r in reciters
        ],
    })


@bp.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        allowed = {"autoplay", "default_reciter", "volume", "repeat_mode"}
        quran_service.update_settings(**{k: v for k, v in data.items() if k in allowed})
    return jsonify(quran_service.get_settings())


@bp.route("/api/folder", methods=["POST"])
def api_folder():
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return jsonify({"error": "برجاء إدخال مسار صحيح"}), 400
    if not os.path.isdir(path):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            return jsonify({"error": "تعذر الوصول إلى هذا المسار"}), 400
    quran_service.set_quran_dir(path)
    reciters = quran_service.get_reciters(force=True)
    return jsonify({"quran_dir": path, "reciter_count": len(reciters)})


@bp.route("/api/state", methods=["GET", "POST"])
def api_state():
    """Persists and returns playback state for the separate player window."""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        allowed = {"last_reciter", "last_surah", "last_position", "last_playing"}
        quran_service.update_settings(**{k: v for k, v in data.items() if k in allowed})
        return jsonify({"ok": True})

    autostart = request.args.get("autostart") == "1"
    settings = quran_service.get_settings()
    if autostart:
        if not settings["autoplay"]:
            return jsonify({"active": False})
        resolved = quran_service.resolve_autostart_track()
        if not resolved or not resolved["track"]:
            return jsonify({"active": False})
        return jsonify({
            "active": True,
            "reciter": {"id": resolved["reciter"]["id"], "name": resolved["reciter"]["name"]},
            "track": resolved["track"],
            "volume": settings["volume"],
            "repeat_mode": settings["repeat_mode"],
            "position": 0,
            "autoplay": True,
        })

    if not settings["last_reciter"] or not settings["last_surah"]:
        return jsonify({"active": False})
    reciter = quran_service.get_reciter(settings["last_reciter"])
    track = quran_service.get_track(settings["last_reciter"], settings["last_surah"]) if reciter else None
    if not reciter or not track:
        return jsonify({"active": False})
    return jsonify({
        "active": True,
        "reciter": {"id": reciter["id"], "name": reciter["name"]},
        "track": track,
        "volume": settings["volume"],
        "repeat_mode": settings["repeat_mode"],
        "position": settings["last_position"],
        "autoplay": settings["last_playing"],
    })


@bp.route("/audio/<reciter_id>/<path:filename>")
def audio(reciter_id, filename):
    path = quran_service.resolve_audio_path(reciter_id, filename)
    if not path:
        abort(404)
    return send_file(path, conditional=True)
