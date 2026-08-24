import json
import os

LANGUAGES = ["en", "ru", "es", "fr", "de"]
LANGUAGE_NAMES = {"en": "English", "ru": "Русский", "es": "Español", "fr": "Français", "de": "Deutsch"}

_LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locale_data")


def _load_locale(code):
    path = os.path.join(_LOCALE_DIR, f"{code}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


LOCALES = {code: _load_locale(code) for code in LANGUAGES}

_current = "en"


def set_language(code):
    global _current
    if code in LOCALES:
        _current = code


def get_language():
    return _current


def t(key, **kwargs):
    table = LOCALES.get(_current) or LOCALES["en"]
    s = table.get(key)
    if s is None:
        s = LOCALES["en"].get(key, key)
    return s.format(**kwargs) if kwargs else s
