import json
import os
import threading
from typing import Optional


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANG_FILE = os.path.join(BASE_DIR, "users_db.json")
LOCALES_DIR = os.path.join(BASE_DIR, "locales")
_lock = threading.Lock()
_cache = {"data": {}, "translations": {}, "time": 0}
CACHE_TTL = 5

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


def _load_users_db():
    global _cache
    import time
    now = time.time()
    if _cache["data"] is not None and (now - _cache["time"]) < CACHE_TTL:
        return _cache["data"]
    
    if os.path.exists(LANG_FILE):
        try:
            with open(LANG_FILE, "r") as f:
                _cache["data"] = json.load(f)
                _cache["time"] = now
                return _cache["data"]
        except:
            pass
    return {}


def _save_users_db(data):
    global _cache
    with _lock:
        try:
            with open(LANG_FILE, "w") as f:
                json.dump(data, f, indent=2)
            _cache["data"] = data
            _cache["time"] = time.time()
        except:
            pass


import time


def get_user_lang(user_id: int) -> str:
    db = _load_users_db()
    user_id_str = str(user_id)
    if user_id_str in db and isinstance(db[user_id_str], dict):
        return db[user_id_str].get("lang", DEFAULT_LANG)
    return DEFAULT_LANG


def set_user_lang(user_id: int, lang: str):
    db = _load_users_db()
    user_id_str = str(user_id)
    if user_id_str not in db:
        db[user_id_str] = {}
    if not isinstance(db[user_id_str], dict):
        db[user_id_str] = {}
    db[user_id_str]["lang"] = lang
    _save_users_db(db)


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


def t(key: str, user_id: Optional[int] = None, lang: Optional[str] = None, **kwargs) -> str:
    if lang:
        pass
    elif user_id:
        lang = get_user_lang(user_id)
        kwargs['user_id'] = user_id
    else:
        lang = DEFAULT_LANG
    
    translations = _load_translations(lang)
    
    nested = _get_nested(translations, key)
    if nested:
        text = nested
    else:
        default_translations = _load_translations(DEFAULT_LANG)
        text = default_translations.get(key, key)
    
    return text.format(**kwargs)


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
