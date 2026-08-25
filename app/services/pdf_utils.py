"""
Pure-Python replacement for the old Playwright-based PDF generation
(see app/services/receipts.py's module docstring for why Playwright was
removed). Uses reportlab to draw PDF pages directly - no bundled
browser, no external binary, nothing that can fail to import inside a
PyInstaller .exe.

Arabic needs THREE things a plain PDF library doesn't do for you:
  1. RESHAPING - Arabic letters change glyph shape depending on their
     position in a word (isolated/initial/medial/final). arabic_reshaper
     converts each letter to the correct joined glyph form.
  2. BIDI REORDERING - Arabic reads right-to-left, but is mixed with
     LTR runs (numbers, English brand names, "ج.م"). python-bidi
     reorders the reshaped text into the correct *visual* left-to-right
     drawing order, which is what reportlab (a purely visual-order
     drawing API) actually needs.
  3. FONT FALLBACK - the bundled Noto Naskh Arabic font (SIL Open Font
     License, app/static/fonts/) covers Arabic + Western digits/
     punctuation, but by Noto's own design deliberately has ZERO Latin
     alphabet glyphs (Noto ships Latin coverage as a *separate* font
     family, meant to be layered). Since receipts mix Arabic with the
     Latin "Al-Qemma" brand name, every draw call below splits the
     already-reordered text into runs and switches to reportlab's
     built-in Helvetica for any run the Arabic font can't render,
     rather than silently dropping those glyphs.

All three run together via the draw_* methods on RTLCanvas - callers
never call rtl() or think about fonts themselves, which removes the
whole class of "forgot to reshape/fell back to tofu" bugs.
"""
import os

import arabic_reshaper
from bidi import get_display
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

_FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "fonts")

FONT_REGULAR = "NotoNaskhArabic"
FONT_BOLD = "NotoNaskhArabic-Bold"
_LATIN_FALLBACK = {FONT_REGULAR: "Helvetica", FONT_BOLD: "Helvetica-Bold"}

_registered = False
_arabic_cmap = None


def _ensure_fonts_registered():
    global _registered, _arabic_cmap
    if _registered:
        return
    pdfmetrics.registerFont(TTFont(FONT_REGULAR, os.path.join(_FONTS_DIR, "NotoNaskhArabic-Regular.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, os.path.join(_FONTS_DIR, "NotoNaskhArabic-Bold.ttf")))
    _arabic_cmap = pdfmetrics.getFont(FONT_REGULAR).face.charWidths
    _registered = True


def rtl(text):
    """Reshape + bidi-reorder any string (Arabic, Latin, or mixed) into
    the correct visual drawing order. Always safe to call, including on
    pure-English/numeric strings."""
    if text is None:
        return ""
    return get_display(arabic_reshaper.reshape(str(text)))


def _font_has_glyph(ch):
    """True if the bundled Arabic font can render this character (its
    own script + digits/punctuation it ships with), False if it's a
    Latin letter that needs the Helvetica fallback instead."""
    return ord(ch) in _arabic_cmap and _arabic_cmap[ord(ch)] != 0


def _split_runs(text, font):
    """Splits already-rtl()-reordered text into consecutive runs of
    (substring, font_to_use). Since get_display() already produced the
    correct left-to-right VISUAL order, runs can just be drawn left to
    right in sequence with no further reordering - only the font
    changes per run."""
    if font not in _LATIN_FALLBACK:
        return [(text, font)]
    runs = []
    current = ""
    current_is_fallback = None
    for ch in text:
        use_fallback = ch != " " and not _font_has_glyph(ch)
        if current_is_fallback is None or use_fallback == current_is_fallback:
            current += ch
        else:
            runs.append((current, _LATIN_FALLBACK[font] if current_is_fallback else font))
            current = ch
        current_is_fallback = use_fallback
    if current:
        runs.append((current, _LATIN_FALLBACK[font] if current_is_fallback else font))
    return runs


def _run_width(runs, size):
    return sum(stringWidth(chunk, f, size) for chunk, f in runs)


def truncate_to_fit(text, font, size, max_width):
    """Binary-searches the longest prefix of `text` (plus an ellipsis)
    that fits within max_width when drawn in `font`/`size`, accounting
    for Arabic reshaping/bidi and the Latin-fallback font switching -
    NOT a naive character-count truncation, which would either cut mid
    -ligature or under/overestimate width for mixed Arabic+Latin text.
    Returns the original text unchanged if it already fits."""
    if text is None:
        return ""
    text = str(text)

    def width_of(s):
        return _run_width(_split_runs(rtl(s), font), size)

    if width_of(text) <= max_width:
        return text

    ellipsis = "…"
    if width_of(ellipsis) > max_width:
        return ellipsis

    lo, hi, best = 0, len(text), ellipsis
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = text[:mid] + ellipsis
        if width_of(candidate) <= max_width:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    return best


class RTLCanvas:
    """Thin wrapper around reportlab's canvas that applies rtl() and
    per-run font-fallback to every string automatically, so call sites
    never have to remember to do it themselves."""

    def __init__(self, path_or_buffer, pagesize=A4):
        _ensure_fonts_registered()
        self.c = canvas.Canvas(path_or_buffer, pagesize=pagesize)
        self.width, self.height = pagesize

    def _draw_runs(self, x, y, runs, size, color):
        self.c.setFillColor(HexColor(color))
        cx = x
        for chunk, f in runs:
            self.c.setFont(f, size)
            self.c.drawString(cx, y, chunk)
            cx += stringWidth(chunk, f, size)

    def draw_right(self, x, y, text, font=FONT_REGULAR, size=11, color="#1A1F2B"):
        """x is the RIGHT edge the text should end at (RTL-natural)."""
        runs = _split_runs(rtl(text), font)
        total_w = _run_width(runs, size)
        self._draw_runs(x - total_w, y, runs, size, color)

    def draw_center(self, x, y, text, font=FONT_REGULAR, size=11, color="#1A1F2B"):
        runs = _split_runs(rtl(text), font)
        total_w = _run_width(runs, size)
        self._draw_runs(x - total_w / 2, y, runs, size, color)

    def draw_left(self, x, y, text, font=FONT_REGULAR, size=11, color="#1A1F2B"):
        runs = _split_runs(rtl(text), font)
        self._draw_runs(x, y, runs, size, color)

    def line(self, x1, y1, x2, y2, color="#E5E5E5", width=0.6):
        self.c.setStrokeColor(HexColor(color))
        self.c.setLineWidth(width)
        self.c.line(x1, y1, x2, y2)

    def rect(self, x, y, w, h, stroke="#E5E5E5", fill=None, radius=4):
        self.c.setStrokeColor(HexColor(stroke))
        if fill:
            self.c.setFillColor(HexColor(fill))
        self.c.roundRect(x, y, w, h, radius, stroke=1, fill=1 if fill else 0)

    def new_page(self):
        self.c.showPage()

    def save(self):
        self.c.save()


def mm_(v):
    return v * mm

