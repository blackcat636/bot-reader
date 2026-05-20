import json
import os

SUPPORTED = {"uk", "en"}
DEFAULT = "en"

_translations: dict[str, dict] = {}


def _load() -> None:
    base = os.path.join(os.path.dirname(__file__), "..", "i18n")
    for lang in SUPPORTED:
        path = os.path.join(base, f"{lang}.json")
        with open(path, encoding="utf-8") as f:
            _translations[lang] = json.load(f)


def normalize(lang_code: str | None) -> str:
    if not lang_code:
        return DEFAULT
    code = lang_code.split("-")[0].lower()
    return code if code in SUPPORTED else DEFAULT


def t(lang: str, key: str, **kwargs) -> str:
    if not _translations:
        _load()
    strings = _translations.get(lang) or _translations.get(DEFAULT, {})
    text = strings.get(key) or _translations.get(DEFAULT, {}).get(key, key)
    return text.format(**kwargs) if kwargs else text
