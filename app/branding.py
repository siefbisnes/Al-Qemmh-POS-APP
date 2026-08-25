"""
Official Al-Qemma branding text, kept out of the template files and out of
the editable Settings table on purpose, per the brief: this should not
exist as a plain, easily-found-and-edited HTML string or a row an
employee could change from the Settings page.

Honest note on what this actually protects against: this is a
self-hosted, offline application that the shop owner fully controls, so
there is no way to stop someone with access to the server files from
eventually finding this if they go looking (the strings have to be
decoded back to plain text somewhere to display them, and that has to
happen in code they can read). What this *does* achieve: the text isn't
sitting as a literal, searchable string in any .html template or in the
database, so it can't be casually edited from the Settings UI, a text
editor opened on a template file, or a database browser pointed at
alqemma.db. Changing it requires deliberately editing this Python file
and knowing what the encoded values mean.
"""
import base64

_ENCODED = {
    "shop_name": "QWwtUWVtbWE=",
    "title_en": "SW52ZW50b3J5LCBTYWxlcywgYW5kIFdhcnJhbnR5IE1hbmFnZW1lbnQ=",
    "title_ar": "2KXYr9in2LHYqSDYp9mE2YXYrtiy2YjZhiDZiNin2YTZhdio2YrYudin2Kog2YjYp9mE2LbZhdin2YY=",
    "tagline_en": "QSBjb21wbGV0ZSBzeXN0ZW0gbWFkZSBieSBTaWVmLg==",
    "tagline_ar": "2YbYuNin2YUg2YXYqtmD2KfZhdmEINmF2YYg2KXZhtiq2KfYrCDYs9mK2YEu",
    "footer_en": "Q29tcGxldGUgU3lzdGVtIG1hZGUgYnkgU2llZg==",
    "phone": "MDEwMjA4ODk5NTE=",
}

_cache = None


def _decode_all():
    global _cache
    if _cache is None:
        _cache = {k: base64.b64decode(v).decode("utf-8") for k, v in _ENCODED.items()}
    return _cache


def get_branding():
    """Returns the decoded branding dict. Call this from Python (routes,
    services, Jinja globals) rather than ever writing the literal text
    into a template."""
    b = _decode_all()
    return {
        "shop_name": b["shop_name"],
        "title_en": b["title_en"],
        "title_ar": b["title_ar"],
        "tagline_en": b["tagline_en"],
        "tagline_ar": b["tagline_ar"],
        "footer_en": b["footer_en"],
        "phone": b["phone"],
        "footer_line": f"{b['footer_en']} \u2014 {b['phone']}",
    }
