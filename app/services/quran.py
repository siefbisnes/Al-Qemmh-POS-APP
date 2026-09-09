"""
Quran library service.

Mirrors the pattern of app/services/*.py used elsewhere in the app:
plain functions, no classes, talking to app.services.settings for
persisted key/value config (the same key/value settings table already
used for theme/tailscale/changelog bookkeeping - see app/__init__.py).

The audio library itself is NEVER stored in the database. The
filesystem (QURAN_DIR) is the single source of truth for which
reciters and tracks exist. We keep a small in-memory cache of the
last scan so the library page doesn't re-walk the disk on every
request (see "Performance" requirements) - it's invalidated by
rescan() and by a directory-change (mtime check).
"""
import os
import re
import threading
import unicodedata

from flask import current_app

from app.services import settings as settings_service

SUPPORTED_EXTENSIONS = (".mp3", ".wav", ".ogg", ".m4a")

# ---------------------------------------------------------------------
# Settings keys (persisted via the existing settings service - a plain
# string key/value store, same as quran_* usage below mirrors
# _CHANGELOG_SETTINGS_KEY in app/__init__.py).
# ---------------------------------------------------------------------
SETTING_QURAN_DIR = "quran_directory"
SETTING_AUTOPLAY = "quran_autoplay"
SETTING_DEFAULT_RECITER = "quran_default_reciter"
SETTING_VOLUME = "quran_volume"
SETTING_REPEAT_MODE = "quran_repeat_mode"
SETTING_LAST_RECITER = "quran_last_reciter"
SETTING_LAST_SURAH = "quran_last_surah"
SETTING_LAST_POSITION = "quran_last_position"
SETTING_LAST_PLAYING = "quran_last_playing"

DEFAULT_VOLUME = 0.20
REPEAT_MODES = ("off", "track", "playlist")

# ---------------------------------------------------------------------
# The 114 surah names, in Mushaf order (index 0 == surah 1 == الفاتحة).
# ---------------------------------------------------------------------
SURAH_NAMES = [
    "الفاتحة", "البقرة", "آل عمران", "النساء", "المائدة", "الأنعام", "الأعراف",
    "الأنفال", "التوبة", "يونس", "هود", "يوسف", "الرعد", "إبراهيم", "الحجر",
    "النحل", "الإسراء", "الكهف", "مريم", "طه", "الأنبياء", "الحج", "المؤمنون",
    "النور", "الفرقان", "الشعراء", "النمل", "القصص", "العنكبوت", "الروم",
    "لقمان", "السجدة", "الأحزاب", "سبأ", "فاطر", "يس", "الصافات", "ص",
    "الزمر", "غافر", "فصلت", "الشورى", "الزخرف", "الدخان", "الجاثية",
    "الأحقاف", "محمد", "الفتح", "الحجرات", "ق", "الذاريات", "الطور", "النجم",
    "القمر", "الرحمن", "الواقعة", "الحديد", "المجادلة", "الحشر", "الممتحنة",
    "الصف", "الجمعة", "المنافقون", "التغابن", "الطلاق", "التحريم", "الملك",
    "القلم", "الحاقة", "المعارج", "نوح", "الجن", "المزمل", "المدثر", "القيامة",
    "الإنسان", "المرسلات", "النبأ", "النازعات", "عبس", "التكوير", "الانفطار",
    "المطففين", "الانشقاق", "البروج", "الطارق", "الأعلى", "الغاشية", "الفجر",
    "البلد", "الشمس", "الليل", "الضحى", "الشرح", "التين", "العلق", "القدر",
    "البينة", "الزلزلة", "العاديات", "القارعة", "التكاثر", "العصر", "الهمزة",
    "الفيل", "قريش", "الماعون", "الكوثر", "الكافرون", "النصر", "المسد",
    "الإخلاص", "الفلق", "الناس",
]

# Latin/transliterated aliases -> 1-based surah number, used as a last
# resort when a filename has neither a number nor Arabic text (e.g.
# "Al Fatiha.mp3", "002_baqarah.mp3"). Not exhaustive - only common
# transliterations - since the number-based path covers the vast
# majority of real-world reciter archives.
_LATIN_ALIASES = {
    "fatiha": 1, "fatihah": 1, "alfatiha": 1,
    "baqarah": 2, "baqara": 2, "albaqarah": 2,
    "imran": 3, "aliimran": 3, "al-imran": 3,
    "nisa": 4, "annisa": 4,
    "maidah": 5, "maeda": 5, "almaidah": 5,
    "anaam": 6, "anam": 6, "alanam": 6,
    "araf": 7, "aaraf": 7, "alaraf": 7,
    "anfal": 8,
    "tawbah": 9, "tauba": 9, "attawbah": 9,
    "yunus": 10,
    "hud": 11,
    "yusuf": 12,
    "rad": 13,
    "ibrahim": 14,
    "hijr": 15,
    "nahl": 16,
    "isra": 17,
    "kahf": 18,
    "maryam": 19,
    "taha": 20,
    "anbiya": 21,
    "hajj": 22,
    "muminun": 23, "mumenoon": 23,
    "nur": 24, "noor": 24,
    "furqan": 25,
    "shuara": 26,
    "naml": 27,
    "qasas": 28,
    "ankabut": 29,
    "rum": 30,
    "luqman": 31,
    "sajdah": 32, "sajda": 32,
    "ahzab": 33,
    "saba": 34,
    "fatir": 35,
    "yasin": 36, "yaseen": 36,
    "saffat": 37,
    "sad": 38,
    "zumar": 39,
    "ghafir": 40,
    "fussilat": 41,
    "shura": 42,
    "zukhruf": 43,
    "dukhan": 44,
    "jathiya": 45,
    "ahqaf": 46,
    "muhammad": 47,
    "fath": 48,
    "hujurat": 49,
    "qaf": 50,
    "dhariyat": 51,
    "tur": 52,
    "najm": 53,
    "qamar": 54,
    "rahman": 55,
    "waqiah": 56,
    "hadid": 57,
    "mujadila": 58,
    "hashr": 59,
    "mumtahina": 60,
    "saff": 61,
    "jumua": 62, "jumuah": 62,
    "munafiqun": 63,
    "taghabun": 64,
    "talaq": 65,
    "tahrim": 66,
    "mulk": 67,
    "qalam": 68,
    "haqqah": 69,
    "maarij": 70,
    "nuh": 71,
    "jinn": 72,
    "muzzammil": 73,
    "muddaththir": 74,
    "qiyamah": 75,
    "insan": 76, "dahr": 76,
    "mursalat": 77,
    "naba": 78,
    "naziat": 79,
    "abasa": 80,
    "takwir": 81,
    "infitar": 82,
    "mutaffifin": 83,
    "inshiqaq": 84,
    "buruj": 85,
    "tariq": 86,
    "ala": 87,
    "ghashiya": 88,
    "fajr": 89,
    "balad": 90,
    "shams": 91,
    "layl": 92,
    "duha": 93,
    "sharh": 94, "inshirah": 94,
    "tin": 95,
    "alaq": 96,
    "qadr": 97,
    "bayyina": 98,
    "zalzalah": 99,
    "adiyat": 100,
    "qariah": 101,
    "takathur": 102,
    "asr": 103,
    "humazah": 104,
    "fil": 105,
    "quraysh": 106, "quraish": 106,
    "maun": 107,
    "kawthar": 108,
    "kafirun": 109,
    "nasr": 110,
    "masad": 111, "lahab": 111,
    "ikhlas": 112,
    "falaq": 113,
    "nas": 114,
}

# Reverse map for detecting an Arabic surah name already present in the
# filename (longest name first, so "آل عمران" isn't shadowed by a
# shorter partial match).
_ARABIC_NAME_TO_NUMBER = {name: i + 1 for i, name in enumerate(SURAH_NAMES)}
_ARABIC_NAMES_BY_LENGTH = sorted(_ARABIC_NAME_TO_NUMBER, key=len, reverse=True)

_scan_lock = threading.Lock()
_cache = {"dir_key": None, "reciters": None}


def _strip_arabic_diacritics(text):
    return "".join(c for c in unicodedata.normalize("NFC", text) if not unicodedata.combining(c))


def _normalize_latin(text):
    text = text.lower()
    return re.sub(r"[^a-z]", "", text)


def surah_name(number):
    if 1 <= number <= 114:
        return SURAH_NAMES[number - 1]
    return None


def detect_surah(filename):
    """Best-effort identification of a track's surah number/name from
    its filename. Returns (number_or_None, display_name).

    Priority, per the feature spec:
      1. A clear leading/standalone surah number (1, 01, 001, "001 - ...").
      2. A recognizable Arabic surah name already in the filename.
      3. A recognizable Latin transliteration of a surah name.
      4. Fallback: the filename itself (extension stripped), so an
         unrecognized file still shows up rather than being hidden.
    """
    stem = os.path.splitext(filename)[0].strip()

    # 1) Numeric detection - a 1-3 digit number at the start of the
    # filename (allowing an optional separator after it), which covers
    # "1.mp3", "01.mp3", "001.mp3", "001 - Al Fatiha.mp3", "002_baqarah.mp3".
    match = re.match(r"^0*(\d{1,3})(?=$|[\s._\-])", stem)
    if match:
        number = int(match.group(1))
        if 1 <= number <= 114:
            return number, surah_name(number)

    # 2) Arabic surah name embedded anywhere in the filename.
    stripped = _strip_arabic_diacritics(stem)
    for name in _ARABIC_NAMES_BY_LENGTH:
        if name in stripped:
            number = _ARABIC_NAME_TO_NUMBER[name]
            return number, name

    # 3) Latin transliteration.
    normalized = _normalize_latin(stem)
    if normalized:
        for alias, number in _LATIN_ALIASES.items():
            if alias in normalized:
                return number, surah_name(number)

    # 4) Unknown - show the raw filename rather than dropping the track.
    return None, stem


def get_quran_dir():
    """Resolve the configured Quran folder, falling back to a sensible
    default next to the app's other user data (same directory family as
    DATABASE_PATH/PRODUCT_IMAGES_DIR, so it also works once packaged as
    an .exe via PyInstaller - see config.BUNDLE_DIR usage in db.py)."""
    configured = settings_service.get(SETTING_QURAN_DIR)
    if configured:
        return configured
    data_dir = os.path.dirname(current_app.config.get("DATABASE_PATH", "."))
    return os.path.join(data_dir, "quran")


def set_quran_dir(path):
    settings_service.set(SETTING_QURAN_DIR, path)
    invalidate_cache()


def ensure_quran_dir():
    path = get_quran_dir()
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def invalidate_cache():
    with _scan_lock:
        _cache["dir_key"] = None
        _cache["reciters"] = None


def _dir_key(path):
    """A cheap fingerprint of the directory tree's top-level mtimes,
    used only to decide whether an already-cached scan is still good
    enough - not a substitute for an explicit rescan, which always
    re-walks the disk unconditionally."""
    try:
        top_mtime = os.stat(path).st_mtime
    except OSError:
        return (path, None)
    sub_mtimes = []
    try:
        for entry in os.scandir(path):
            if entry.is_dir():
                try:
                    sub_mtimes.append((entry.name, entry.stat().st_mtime))
                except OSError:
                    continue
    except OSError:
        pass
    return (path, top_mtime, tuple(sorted(sub_mtimes)))


def _scan(path):
    reciters = []
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
    except OSError:
        return reciters

    for entry in entries:
        if not entry.is_dir():
            continue
        reciter_id = entry.name
        display_name = entry.name.replace("_", " ").strip() or entry.name
        tracks = []
        try:
            files = sorted(os.scandir(entry.path), key=lambda e: e.name.lower())
        except OSError:
            files = []
        for f in files:
            if not f.is_file():
                continue
            ext = os.path.splitext(f.name)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            number, name = detect_surah(f.name)
            tracks.append({
                "filename": f.name,
                "surah_number": number,
                "surah_name": name,
            })

        # Known surahs first, in Quran order; unrecognized files after,
        # alphabetically - so a mixed-naming reciter folder still reads
        # as a sensible playlist instead of raw filesystem order.
        tracks.sort(key=lambda t: (t["surah_number"] is None, t["surah_number"] or 0, t["filename"]))

        if not tracks:
            continue

        reciters.append({
            "id": reciter_id,
            "name": display_name,
            "track_count": len(tracks),
            "tracks": tracks,
        })

    return reciters


def get_reciters(force=False):
    """Returns the cached (or freshly scanned) list of reciters. Each
    reciter dict: {id, name, track_count, tracks: [...]}."""
    path = ensure_quran_dir()
    key = _dir_key(path)
    with _scan_lock:
        if not force and _cache["reciters"] is not None and _cache["dir_key"] == key:
            return _cache["reciters"]
    reciters = _scan(path)
    with _scan_lock:
        _cache["dir_key"] = key
        _cache["reciters"] = reciters
    return reciters


def rescan():
    invalidate_cache()
    return get_reciters(force=True)


def get_reciter(reciter_id):
    for r in get_reciters():
        if r["id"] == reciter_id:
            return r
    return None


def get_track(reciter_id, filename):
    reciter = get_reciter(reciter_id)
    if not reciter:
        return None
    for t in reciter["tracks"]:
        if t["filename"] == filename:
            return t
    return None


def resolve_audio_path(reciter_id, filename):
    """Safely resolve a reciter/filename pair to an on-disk path,
    refusing anything that isn't an existing file inside the current
    Quran directory (blocks path traversal via crafted filenames)."""
    base = os.path.realpath(ensure_quran_dir())
    candidate = os.path.realpath(os.path.join(base, reciter_id, filename))
    if os.path.commonpath([base, candidate]) != base:
        return None
    if not os.path.isfile(candidate):
        return None
    ext = os.path.splitext(candidate)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return None
    return candidate


# ---------------------------------------------------------------------
# Settings (autoplay / default reciter / volume / repeat / resume state)
# ---------------------------------------------------------------------

def get_settings():
    reciters = get_reciters()
    reciter_ids = [r["id"] for r in reciters]

    default_reciter = settings_service.get(SETTING_DEFAULT_RECITER)
    if default_reciter not in reciter_ids:
        default_reciter = reciter_ids[0] if reciter_ids else None

    last_reciter = settings_service.get(SETTING_LAST_RECITER)
    if last_reciter not in reciter_ids:
        last_reciter = None

    try:
        volume = float(settings_service.get(SETTING_VOLUME) or DEFAULT_VOLUME)
    except (TypeError, ValueError):
        volume = DEFAULT_VOLUME
    volume = min(1.0, max(0.0, volume))

    repeat_mode = settings_service.get(SETTING_REPEAT_MODE)
    if repeat_mode not in REPEAT_MODES:
        repeat_mode = "off"

    try:
        last_position = float(settings_service.get(SETTING_LAST_POSITION) or 0)
    except (TypeError, ValueError):
        last_position = 0.0

    return {
        "quran_dir": get_quran_dir(),
        "autoplay": settings_service.get(SETTING_AUTOPLAY) == "1",
        "default_reciter": default_reciter,
        "volume": volume,
        "repeat_mode": repeat_mode,
        "last_reciter": last_reciter,
        "last_surah": settings_service.get(SETTING_LAST_SURAH),
        "last_position": last_position,
        "last_playing": settings_service.get(SETTING_LAST_PLAYING) == "1",
    }


def update_settings(**kwargs):
    if "autoplay" in kwargs:
        settings_service.set(SETTING_AUTOPLAY, "1" if kwargs["autoplay"] else "0")
    if "default_reciter" in kwargs:
        settings_service.set(SETTING_DEFAULT_RECITER, kwargs["default_reciter"] or "")
    if "volume" in kwargs:
        volume = min(1.0, max(0.0, float(kwargs["volume"])))
        settings_service.set(SETTING_VOLUME, str(volume))
    if "repeat_mode" in kwargs:
        mode = kwargs["repeat_mode"] if kwargs["repeat_mode"] in REPEAT_MODES else "off"
        settings_service.set(SETTING_REPEAT_MODE, mode)
    if "last_reciter" in kwargs:
        settings_service.set(SETTING_LAST_RECITER, kwargs["last_reciter"] or "")
    if "last_surah" in kwargs:
        settings_service.set(SETTING_LAST_SURAH, kwargs["last_surah"] or "")
    if "last_position" in kwargs:
        settings_service.set(SETTING_LAST_POSITION, str(float(kwargs["last_position"])))
    if "last_playing" in kwargs:
        settings_service.set(SETTING_LAST_PLAYING, "1" if kwargs["last_playing"] else "0")


def resolve_autostart_track():
    """Used on app open (Auto Start) and by the resume-state endpoint:
    picks the main reciter, falling back to the first available one if
    it was deleted, and the last-played surah if still present, else
    the first track."""
    reciters = get_reciters()
    if not reciters:
        return None

    settings = get_settings()
    reciter = get_reciter(settings["default_reciter"]) if settings["default_reciter"] else None
    if reciter is None:
        reciter = reciters[0]  # main reciter unavailable -> fallback

    track = None
    if settings["last_reciter"] == reciter["id"] and settings["last_surah"]:
        track = get_track(reciter["id"], settings["last_surah"])
    if track is None and reciter["tracks"]:
        track = reciter["tracks"][0]

    return {"reciter": reciter, "track": track, "settings": settings}
