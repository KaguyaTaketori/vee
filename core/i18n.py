import os
import json
import sqlite3
from typing import Optional

from core.db import DB_PATH


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALES_DIR = os.path.join(BASE_DIR, "locales")
_cache = {"translations": {}}

DEFAULT_LANG = "en"

LANGUAGES = {
    "en": "English",
    "zh": "中文",
    "ja": "日本語",
    "ko": "한국어",
}


def _load_translations(lang: str) -> dict:
    global _cache
    
    if lang in _cache["translations"]:
        return _cache["translations"][lang]
    
    file_path = os.path.join(LOCALES_DIR, f"{lang}.json")
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                _cache["translations"][lang] = json.load(f)
                return _cache["translations"][lang]
        except Exception:
            pass
    
    if lang != DEFAULT_LANG:
        return _load_translations(DEFAULT_LANG)
    
    return {}

_lang_cache: dict[int, str] = {}

def get_user_lang(user_id: int) -> str:
    return _lang_cache.get(user_id, DEFAULT_LANG)

async def warm_user_lang(user_id: int) -> str:
    from core.db import get_db
    async with get_db() as db:
        async with db.execute(
            "SELECT lang FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            lang = row[0] if row and row[0] else DEFAULT_LANG
    _lang_cache[user_id] = lang
    return lang

async def set_user_lang(user_id: int, lang: str):
    _lang_cache[user_id] = lang
    from core import users
    await users.set_user_lang(user_id, lang)


def _get_nested(translations: dict, key: str) -> Optional[str]:
    keys = key.split(".")
    value = translations
    for k in keys:
        if isinstance(value, dict):
            value = value.get(k)
        else:
            return None
    return value if isinstance(value, str) else None


def _get_plural(translations: dict, key: str, count: int) -> Optional[str]:
    if count == 1:
        plural_key = key
    else:
        plural_key = f"{key}_plural"
    
    result = translations.get(plural_key)
    if result and "{count}" in result:
        return result.replace("{count}", str(count))
    return result if result else str(count)


def detect_system_lang(lang_code: str) -> str:
    lang_code = lang_code.lower().replace("-", "_")
    
    if lang_code in LANGUAGES:
        return lang_code
    
    primary = lang_code.split("_")[0]
    if primary in LANGUAGES:
        return primary
    
    lang_map = {
        "zh": "zh", "zhcn": "zh", "zhtw": "zh", "zhhk": "zh",
        "ja": "ja",
        "ko": "ko",
        "es": "es", "sp": "es",
        "fr": "fr",
        "de": "de",
        "pt": "pt", "ptbr": "pt", "ptpt": "pt",
        "ru": "ru",
        "ar": "ar",
        "hi": "hi",
        "id": "id", "in": "id",
        "vi": "vi",
        "th": "th",
    }
    
    return lang_map.get(primary, DEFAULT_LANG)


def t(key: str, user_id: int | None = None, lang: str | None = None, **kwargs) -> str:
    if not lang:
        lang = get_user_lang(user_id) if user_id else DEFAULT_LANG

    if user_id:
        kwargs.setdefault('user_id', user_id)

    translations = _load_translations(lang)
    text = _get_nested(translations, key)

    if not text:
        text = _get_nested(_load_translations(DEFAULT_LANG), key) or key

    try:
        return text.format(**kwargs)
    except KeyError as e:
        logger.warning(f"Missing i18n placeholder {e} for key '{key}'")
        return text

def tp(key: str, count: int, user_id: Optional[int] = None, **kwargs) -> str:
    if user_id:
        lang = get_user_lang(user_id)
    else:
        lang = DEFAULT_LANG
    
    translations = _load_translations(lang)
    
    plural_text = _get_plural(translations, key, count)
    
    if plural_text and "{count}" in plural_text:
        return plural_text.replace("{count}", str(count))
    elif plural_text:
        return plural_text
    
    fallback = _get_plural(_load_translations(DEFAULT_LANG), key, count)
    if fallback and "{count}" in fallback:
        return fallback.replace("{count}", str(count))
    elif fallback:
        return fallback
    
    return str(count)
