import os
import json
import logging
from typing import Optional
from cachetools import LRUCache
from database.db import DB_PATH
from database.users import fetch_user_lang_from_db

logger = logging.getLogger(__name__)

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


_lang_cache: LRUCache[int, str] = LRUCache(maxsize=10_000) 


def get_user_lang(user_id: int) -> str:
    return _lang_cache.get(user_id, DEFAULT_LANG)


async def get_user_lang_async(user_id: int) -> str:
    if user_id in _lang_cache:
        return _lang_cache[user_id]
    return await warm_user_lang(user_id)


async def warm_user_lang(user_id: int) -> str:
    lang = await fetch_user_lang_from_db(user_id)
    _lang_cache[user_id] = lang
    return lang


async def set_user_lang(user_id: int, lang: str):
    _lang_cache[user_id] = lang
    from database.users import set_user_lang
    await set_user_lang(user_id, lang)


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
        if user_id:
            lang = get_user_lang(user_id)
        else:
            lang = DEFAULT_LANG

    if user_id:
        kwargs.setdefault('user_id', user_id)

    translations = _load_translations(lang)
    text = _get_nested(translations, key)

    if not text:
        text = _get_nested(_load_translations(DEFAULT_LANG), key)
        if text and lang != DEFAULT_LANG:
            logger.info(f"Missing translation for '{key}' in {lang}, falling back to {DEFAULT_LANG}")

    if not text:
        logger.warning(f"Missing translation key: '{key}'")
        return key

    try:
        return text.format(**kwargs)
    except KeyError as e:
        logger.warning(f"Missing i18n placeholder {e} for key '{key}' in lang '{lang}' (got kwargs: {list(kwargs.keys())})")
        return text

    
def tp(key: str, count: int, user_id: Optional[int] = None, lang: Optional[str] = None, **kwargs) -> str:
    if not lang:
        lang = get_user_lang(user_id) if user_id else DEFAULT_LANG
    
    translations = _load_translations(lang)
    
    plural_text = _get_plural(translations, key, count)
    
    if plural_text:
        if "{count}" in plural_text:
            return plural_text.replace("{count}", str(count))
        return plural_text
    
    if lang != DEFAULT_LANG:
        fallback = _get_plural(_load_translations(DEFAULT_LANG), key, count)
        if fallback:
            if "{count}" in fallback:
                return fallback.replace("{count}", str(count))
            return fallback
    
    return str(count)


def get_command_description(command: str, scope: str, lang: str) -> str:
    translations = _load_translations(lang)

    desc = (
        translations
        .get("commands", {})
        .get(scope, {})
        .get(command)
    )

    if desc:
        return desc

    if lang != DEFAULT_LANG:
        fallback = _load_translations(DEFAULT_LANG)
        desc = (
            fallback
            .get("commands", {})
            .get(scope, {})
            .get(command)
        )
        if desc:
            return desc

    return command
