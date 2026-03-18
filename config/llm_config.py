# config/llm_config.py
"""
config/llm_config.py
────────────────────
LLM 配置加载器。两层配置：

  1. llm.yaml  — 全部结构配置，包括 active_provider（/setllm 命令直接写回此文件）
  2. .env      — 实际 API Key（通过 keys_env 变量名读取，不进 yaml）

对外暴露：
  build_llm_manager_from_yaml() -> LLMManager   # bootstrap 调用
  set_active_provider(name: str) -> None         # /setllm 命令调用
  get_available_providers() -> list[str]         # /setllm 展示用
"""
from __future__ import annotations

import logging
import os

from ruamel.yaml import YAML

from shared.integrations.llm.manager import LLMManager

_yaml = YAML()
_yaml.preserve_quotes = True

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_YAML_PATH = os.path.join(_BASE_DIR, "llm.yaml")


# ---------------------------------------------------------------------------
# YAML read / write
# ---------------------------------------------------------------------------

def _load_yaml():
    """加载 llm.yaml，返回 ruamel CommentedMap（保留注释）。"""
    if not os.path.exists(_YAML_PATH):
        raise FileNotFoundError(
            f"llm.yaml not found at {_YAML_PATH}. "
            "Copy llm.yaml.example and configure your providers."
        )
    with open(_YAML_PATH, encoding="utf-8") as f:
        return _yaml.load(f) or {}


def _save_yaml(data) -> None:
    """将 data 写回 llm.yaml，注释和格式完整保留。"""
    with open(_YAML_PATH, "w", encoding="utf-8") as f:
        _yaml.dump(data, f)
    logger.info("llm.yaml updated: active_provider=%s", data.get("active_provider"))


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------

def _resolve_keys(provider_conf: dict, provider_name: str) -> list[str]:
    """从 keys_env 指定的环境变量中读取 key 列表。"""
    keys_env = provider_conf.get("keys_env", "")
    if not keys_env:
        logger.warning("Provider '%s' has no keys_env configured", provider_name)
        return []
    raw = os.getenv(keys_env, "")
    if not raw:
        logger.warning(
            "Provider '%s': env var '%s' is empty or not set", provider_name, keys_env
        )
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_llm_manager_from_yaml() -> LLMManager:
    """从 llm.yaml + .env 构建 LLMManager，active_provider 直接读 yaml。"""
    raw = _load_yaml()
    active = raw.get("active_provider", "openai")
    blacklist_ttl = float(raw.get("blacklist_ttl", 60))

    providers: dict = {}
    for name, pconf in raw.get("providers", {}).items():
        if not isinstance(pconf, dict):
            continue
        keys = _resolve_keys(pconf, name)
        if not keys:
            logger.warning("Provider '%s' skipped: no API keys available", name)
            continue
        providers[name] = {
            "model": pconf.get("model", ""),
            "keys": keys,
            "base_url": pconf.get("base_url", ""),
        }

    if not providers:
        raise EnvironmentError(
            "No LLM providers configured. "
            "Check llm.yaml and ensure keys_env variables are set in .env"
        )

    if active not in providers:
        fallback = next(iter(providers))
        logger.warning(
            "active_provider '%s' not available, falling back to '%s'", active, fallback
        )
        active = fallback

    return LLMManager({
        "active_provider": active,
        "blacklist_ttl": blacklist_ttl,
        "providers": providers,
    })


def set_active_provider(name: str) -> None:
    """
    热切换 active_provider 并直接写回 llm.yaml。
    由 /setllm 命令调用。
    """
    import shared.integrations.llm.manager as llm_mod
    if llm_mod.llm_manager is None:
        raise RuntimeError("LLM manager not initialised")
    llm_mod.llm_manager.switch_provider(name)  # 验证合法性，失败抛 ValueError
    raw = _load_yaml()
    raw["active_provider"] = name
    _save_yaml(raw)
    logger.info("Active LLM provider switched to: %s", name)


def get_available_providers() -> list[str]:
    """返回 llm.yaml 中配置了有效 key 的 provider 列表，供 /setllm 展示。"""
    try:
        raw = _load_yaml()
    except FileNotFoundError:
        return []
    return [
        name for name, pconf in raw.get("providers", {}).items()
        if isinstance(pconf, dict) and _resolve_keys(pconf, name)
    ]
