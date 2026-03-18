"""
llm/manager.py

多 Provider、多 Key 的 LLM 管理层。
支持 Round-Robin 轮询、429 自动 Blacklist + TTL、跨 Key 容灾重试。

使用示例：
    from llm.manager import llm_manager
    result = await llm_manager.chat(messages=[{"role": "user", "content": "Hello"}])
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置数据结构
# ---------------------------------------------------------------------------

@dataclass
class ProviderConfig:
    name: str
    model: str
    keys: list[str]
    base_url: str
    extra_headers: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 内置 Provider 适配器（统一 Request/Response 格式）
# ---------------------------------------------------------------------------

class _BaseAdapter:
    """将各 Provider 的请求/响应格式统一为内部格式。"""

    def build_request(
        self,
        model: str,
        messages: list[dict],
        api_key: str,
        **kwargs,
    ) -> tuple[str, dict, dict]:
        """返回 (url, headers, body)"""
        raise NotImplementedError

    def parse_response(self, data: dict) -> str:
        """从响应 JSON 中提取文本内容。"""
        raise NotImplementedError


class _OpenAIAdapter(_BaseAdapter):
    def build_request(self, model, messages, api_key, base_url="https://api.openai.com", **kwargs):
        url = f"{base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {"model": model, "messages": messages, **kwargs}
        return url, headers, body

    def parse_response(self, data: dict) -> str:
        return data["choices"][0]["message"]["content"]


class _AnthropicAdapter(_BaseAdapter):
    def build_request(self, model, messages, api_key, base_url="https://api.anthropic.com", **kwargs):
        url = f"{base_url}/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        # Anthropic 的 system 消息需单独提取
        system = None
        filtered = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                filtered.append(m)
        body: dict[str, Any] = {"model": model, "messages": filtered, "max_tokens": kwargs.pop("max_tokens", 1024), **kwargs}
        if system:
            body["system"] = system
        return url, headers, body

    def parse_response(self, data: dict) -> str:
        return data["content"][0]["text"]


class _GeminiAdapter(_BaseAdapter):
    def build_request(self, model, messages, api_key, base_url="https://generativelanguage.googleapis.com", **kwargs):
        url = f"{base_url}/v1beta/models/{model}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        # 转换为 Gemini contents 格式
        contents = [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in messages if m["role"] != "system"
        ]
        body = {"contents": contents}
        return url, headers, body

    def parse_response(self, data: dict) -> str:
        return data["candidates"][0]["content"]["parts"][0]["text"]


_ADAPTERS: dict[str, _BaseAdapter] = {
    "openai": _OpenAIAdapter(),
    "anthropic": _AnthropicAdapter(),
    "gemini": _GeminiAdapter(),
}


# ---------------------------------------------------------------------------
# Key 状态管理（Round-Robin + Blacklist）
# ---------------------------------------------------------------------------

@dataclass
class _KeyState:
    key: str
    blacklisted_until: float = 0.0   # Unix timestamp；0 表示可用

    def is_available(self) -> bool:
        return time.monotonic() >= self.blacklisted_until

    def blacklist(self, ttl_seconds: float = 60.0) -> None:
        self.blacklisted_until = time.monotonic() + ttl_seconds
        logger.warning("API key ...%s blacklisted for %.0fs", self.key[-6:], ttl_seconds)

    def reset(self) -> None:
        self.blacklisted_until = 0.0


class _ProviderState:
    """单个 Provider 的运行时状态：维护 Key 列表与轮询指针。"""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self._keys: list[_KeyState] = [_KeyState(k) for k in config.keys]
        self._index: int = 0
        self._lock = asyncio.Lock()

    async def next_key(self) -> Optional[_KeyState]:
        """Round-Robin 取下一个可用 Key；全部不可用则返回 None。"""
        async with self._lock:
            total = len(self._keys)
            for _ in range(total):
                state = self._keys[self._index % total]
                self._index = (self._index + 1) % total
                if state.is_available():
                    return state
            return None

    async def all_keys_tried(self) -> list[_KeyState]:
        """返回所有 Key（用于容灾全量重试）。"""
        async with self._lock:
            return list(self._keys)


# ---------------------------------------------------------------------------
# LLMManager 主类
# ---------------------------------------------------------------------------

class LLMManager:
    """
    统一的 LLM 调用入口。

    配置示例（传入 __init__）：
        {
            "active_provider": "openai",
            "blacklist_ttl": 60,
            "providers": {
                "openai": {
                    "model": "gpt-4o",
                    "base_url": "https://api.openai.com",
                    "keys": ["sk-aaa", "sk-bbb"],
                },
                "anthropic": {
                    "model": "claude-3-5-sonnet-20241022",
                    "keys": ["sk-ant-xxx"],
                },
            }
        }
    """

    def __init__(self, config: dict) -> None:
        self._blacklist_ttl: float = float(config.get("blacklist_ttl", 60))
        self._active_provider_name: str = config["active_provider"]
        self._states: dict[str, _ProviderState] = {}

        for name, pconf in config.get("providers", {}).items():
            adapter_key = name.split("_")[0]   # 支持 "openai_backup" → adapter "openai"
            if adapter_key not in _ADAPTERS:
                logger.warning("Unknown provider adapter '%s', skipping.", name)
                continue
            pc = ProviderConfig(
                name=name,
                model=pconf["model"],
                keys=pconf.get("keys", []),
                base_url=pconf.get("base_url", self._default_base_url(adapter_key)),
                extra_headers=pconf.get("extra_headers", {}),
            )
            self._states[name] = _ProviderState(pc)

        if self._active_provider_name not in self._states:
            raise ValueError(f"active_provider '{self._active_provider_name}' not found in providers config.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict],
        provider: Optional[str] = None,
        timeout: float = 30.0,
        **kwargs,
    ) -> str:
        """
        发送 chat 请求，自动处理 Key 轮询与容灾。

        :param messages: OpenAI 格式的消息列表。
        :param provider: 指定 Provider 名称；None 则使用 active_provider。
        :param timeout: HTTP 超时（秒）。
        :param kwargs: 透传给 API（如 temperature、max_tokens）。
        :returns: 模型返回的文本内容。
        :raises RuntimeError: 所有 Key 均失败时抛出。
        """
        provider_name = provider or self._active_provider_name
        state = self._states.get(provider_name)
        if not state:
            raise ValueError(f"Provider '{provider_name}' not configured.")

        adapter_key = provider_name.split("_")[0]
        adapter = _ADAPTERS[adapter_key]
        config = state.config

        errors: list[str] = []
        tried: set[str] = set()

        # 最多尝试所有 Key 一轮
        for _ in range(len(config.keys) + 1):
            key_state = await state.next_key()
            if key_state is None or key_state.key in tried:
                break
            tried.add(key_state.key)

            try:
                text = await self._call_once(
                    adapter=adapter,
                    config=config,
                    api_key=key_state.key,
                    messages=messages,
                    timeout=timeout,
                    **kwargs,
                )
                logger.debug("LLM call succeeded via provider=%s key=...%s", provider_name, key_state.key[-6:])
                return text

            except _RateLimitError as e:
                logger.warning("Rate-limited on key ...%s: %s", key_state.key[-6:], e)
                key_state.blacklist(self._blacklist_ttl)
                errors.append(f"429 key=...{key_state.key[-6:]}")

            except _ProviderError as e:
                logger.error("Provider error on key ...%s: %s", key_state.key[-6:], e)
                errors.append(str(e))
                # 非限流错误也跳过该 Key，继续尝试下一个
                key_state.blacklist(self._blacklist_ttl / 2)

        raise RuntimeError(
            f"All keys for provider '{provider_name}' exhausted. Errors: {errors}"
        )

    def switch_provider(self, provider_name: str) -> None:
        """动态切换默认 Provider。"""
        if provider_name not in self._states:
            raise ValueError(f"Provider '{provider_name}' not configured.")
        self._active_provider_name = provider_name
        logger.info("Active provider switched to: %s", provider_name)

    def get_status(self) -> dict:
        """返回所有 Provider/Key 的当前状态（用于监控/调试）。"""
        now = time.monotonic()
        result = {}
        for pname, state in self._states.items():
            keys_info = []
            for ks in state._keys:
                remaining = max(0.0, ks.blacklisted_until - now)
                keys_info.append({
                    "key_suffix": ks.key[-6:],
                    "available": ks.is_available(),
                    "blacklisted_seconds_remaining": round(remaining, 1),
                })
            result[pname] = {
                "model": state.config.model,
                "active": pname == self._active_provider_name,
                "keys": keys_info,
            }
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _call_once(
        self,
        adapter: _BaseAdapter,
        config: ProviderConfig,
        api_key: str,
        messages: list[dict],
        timeout: float,
        **kwargs,
    ) -> str:
        url, headers, body = adapter.build_request(
            model=config.model,
            messages=messages,
            api_key=api_key,
            base_url=config.base_url,
            **kwargs,
        )
        headers.update(config.extra_headers)

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=body)

        if resp.status_code == 429:
            raise _RateLimitError(f"HTTP 429: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise _ProviderError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        return adapter.parse_response(data)

    @staticmethod
    def _default_base_url(adapter_key: str) -> str:
        defaults = {
            "openai": "https://api.openai.com",
            "anthropic": "https://api.anthropic.com",
            "gemini": "https://generativelanguage.googleapis.com",
        }
        return defaults.get(adapter_key, "")


# ---------------------------------------------------------------------------
# 内部异常
# ---------------------------------------------------------------------------

class _RateLimitError(Exception):
    """HTTP 429 限流。"""

class _ProviderError(Exception):
    """其他 Provider 错误。"""


# ---------------------------------------------------------------------------
# 全局单例（从环境/配置加载）
# ---------------------------------------------------------------------------

def build_llm_manager_from_env() -> LLMManager:
    """
    从环境变量构建 LLMManager 单例。

    所需环境变量（以 OpenAI 为例）：
        LLM_ACTIVE_PROVIDER=openai
        LLM_OPENAI_MODEL=gpt-4o
        LLM_OPENAI_KEYS=sk-aaa,sk-bbb
        LLM_ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
        LLM_ANTHROPIC_KEYS=sk-ant-xxx

    也可直接在 config/settings.py 中声明后传入 build_llm_manager()。
    """
    import os

    active = os.getenv("LLM_ACTIVE_PROVIDER", "openai")
    blacklist_ttl = float(os.getenv("LLM_BLACKLIST_TTL", "60"))

    _DEFAULT_BASE_URLS = {
        "openai":    "https://api.openai.com",
        "anthropic": "https://api.anthropic.com",
        "gemini":    "https://generativelanguage.googleapis.com",
        "groq":      "https://api.groq.com",
    }

    supported = ["openai", "anthropic", "gemini", "groq"]
    providers: dict = {}

    for p in supported:
        keys_raw = os.getenv(f"LLM_{p.upper()}_KEYS", "")
        model = os.getenv(f"LLM_{p.upper()}_MODEL", "")
        if keys_raw and model:
            providers[p] = {
                "model": model,
                "keys": [k.strip() for k in keys_raw.split(",") if k.strip()],
                "base_url": os.getenv(f"LLM_{p.upper()}_BASE_URL", _DEFAULT_BASE_URLS.get(p, "")),
            }

    if not providers:
        raise EnvironmentError("No LLM provider configured. Set LLM_<PROVIDER>_KEYS and LLM_<PROVIDER>_MODEL.")

    return LLMManager({
        "active_provider": active,
        "blacklist_ttl": blacklist_ttl,
        "providers": providers,
    })


# 模块级单例，在 bot 初始化时替换或直接使用
llm_manager: Optional[LLMManager] = None

