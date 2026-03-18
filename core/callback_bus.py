# core/callback_bus.py
"""
全局 InlineKeyboard 回调路由总线。

设计目标
--------
1. 模块间零耦合：downloader 和 billing 各自 @register，无需互相 import。
2. Telegram 零渗透：注册进来的 handler 签名为
       async def handler(ctx: CallbackContext) -> None
   不再接触任何 telegram.* 对象。PTB 的 CallbackQuery / context 全部在
   本模块的 PTB 入口 handle_callback() 中完成包裹，永不外泄。

CallbackContext（平台无关接口）
--------------------------------
提供 handler 所需的全部能力：

    ctx.data          — callback_data 字符串
    ctx.user_id       — 发起方用户 ID
    ctx.user          — 原始用户对象（通常是 telegram.User）
    ctx.platform_ctx  — PlatformContext（send / edit / send_keyboard …）
    ctx.raw_context   — PTB CallbackContext（仅 _cb_download_select 等需要
                        bot_data / user_data 的 handler 使用，其余无需碰它）

    await ctx.answer(text="", show_alert=False)  — 透传给 query.answer()
    await ctx.answer_alert(text)                 — 弹窗形式 answer
    await ctx.delete_message()                   — 删除当前消息

注册时机
--------
模块的 handlers 文件在 setup() 中被 import 时，模块级 @register
装饰器自动执行，副作用式地将 handler 追加到 _HANDLERS 列表。
只要 setup() 在 app.add_handler 之前完成，顺序就是安全的。

用法示例
--------
注册（在各模块 handlers 文件顶层）：

    from core.callback_bus import register

    @register(lambda d: d.startswith("bill_confirm:"))
    async def _cb_bill_confirm(ctx: CallbackContext) -> None:
        entry = await bill_cache.get(ctx.data.split(":")[1])
        await ctx.platform_ctx.edit("✅ 记账成功！")

挂载（在 DownloaderModule.setup() 里，只需一次）：

    from core.callback_bus import handle_callback
    from telegram.ext import CallbackQueryHandler
    app.add_handler(CallbackQueryHandler(handle_callback))
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Platform-agnostic callback context
# ---------------------------------------------------------------------------

class CallbackContext(ABC):
    """
    平台无关的回调上下文，传递给所有注册 handler。

    子类把平台特定操作（query.answer、query.delete_message 等）
    封装在这里；handler 业务逻辑只与此接口交互。
    """

    # ── 基础属性 ──────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def data(self) -> str:
        """callback_data 字符串。"""
        ...

    @property
    @abstractmethod
    def user_id(self) -> int:
        """发起方用户 ID。"""
        ...

    @property
    @abstractmethod
    def user(self) -> Any:
        """原始用户对象（telegram.User 或其他平台等价物）。"""
        ...

    @property
    @abstractmethod
    def platform_ctx(self):
        """PlatformContext — send / edit / send_keyboard / send_markdown 等。"""
        ...

    @property
    @abstractmethod
    def raw_context(self) -> Any:
        """
        平台原始 context（PTB CallbackContext）。
        仅在业务代码需要访问 bot_data / user_data 时使用；
        能不用就不用，以保持可测试性。
        """
        ...

    # ── 操作方法 ──────────────────────────────────────────────────────────

    @abstractmethod
    async def answer(self, text: str = "", *, show_alert: bool = False) -> None:
        """ACK 这次回调。text 为空时静默 ack。"""
        ...

    async def answer_alert(self, text: str) -> None:
        """弹窗形式 ACK（show_alert=True 的快捷方式）。"""
        await self.answer(text, show_alert=True)

    @abstractmethod
    async def delete_message(self) -> None:
        """删除触发此回调的消息。"""
        ...

    def create_sender(self, processing_msg: Any) -> Any:
        """Return a platform-specific BotSender anchored to this callback's message.

        The returned object satisfies the ``BotSender`` Protocol and can be
        passed to ``DownloadFacade.enqueue`` / ``send_cached``.

        Returns ``None`` for non-PTB implementations or when construction is
        not possible — callers should treat None as "no sender available".
        """
        return None

    async def request_text_input(
        self,
        prompt: str,
        state_key: str,
        *,
        placeholder: str = "",
    ) -> None:
        """Ask the user to type a reply and remember *state_key* for the reply handler.

        Platform-agnostic contract
        --------------------------
        Sends a message containing *prompt* that forces the user to reply
        (i.e. a ForceReply on Telegram, or an equivalent on other platforms).
        When the user replies, the reply handler can retrieve *state_key*
        from the session / user_data to know what to do with the input.

        Parameters
        ----------
        prompt:
            Message text shown to the user.
        state_key:
            An opaque string stored in per-user session storage so the reply
            handler knows which workflow to continue.  On Telegram this is
            written to ``context.user_data["text_input_state"]``.
        placeholder:
            Hint text shown inside the reply input box (optional).
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement request_text_input()"
        )


# ---------------------------------------------------------------------------
# PTB (python-telegram-bot) implementation
# ---------------------------------------------------------------------------

class TelegramCallbackContext(CallbackContext):
    """
    由 PTB CallbackQuery + CallbackContext 构造的具体实现。

    所有 telegram.* import 限制在此类内部。
    """

    def __init__(self, query: Any, ptb_context: Any) -> None:
        self._query = query
        self._ptb_context = ptb_context
        # 延迟构造 PlatformContext，避免循环 import
        self._platform_ctx = None

    # ── CallbackContext 属性 ──────────────────────────────────────────────

    @property
    def data(self) -> str:
        return self._query.data or ""

    @property
    def user_id(self) -> int:
        return self._query.from_user.id

    @property
    def user(self) -> Any:
        return self._query.from_user

    @property
    def platform_ctx(self):
        if self._platform_ctx is None:
            from shared.services.platform_context import TelegramContext
            self._platform_ctx = TelegramContext.from_callback_query(
                self._query, self._ptb_context
            )
        return self._platform_ctx

    @property
    def raw_context(self) -> Any:
        return self._ptb_context

    # ── 操作方法 ──────────────────────────────────────────────────────────

    async def answer(self, text: str = "", *, show_alert: bool = False) -> None:
        await self._query.answer(text or None, show_alert=show_alert)

    async def delete_message(self) -> None:
        await self._query.delete_message()

    def create_sender(self, processing_msg: Any) -> Any:
        """Return a TelegramSender whose reply-target is the callback's message."""
        from modules.downloader.strategies.sender import TelegramSender
        return TelegramSender.from_callback(self._query, processing_msg)

    async def request_text_input(
        self,
        prompt: str,
        state_key: str,
        *,
        placeholder: str = "",
    ) -> None:
        """Send a ForceReply prompt and store *state_key* in PTB user_data."""
        from telegram import ForceReply
        self._ptb_context.user_data["text_input_state"] = state_key
        await self._ptb_context.bot.send_message(
            chat_id=self._query.from_user.id,
            text=prompt,
            parse_mode="Markdown",
            reply_markup=ForceReply(
                selective=True,
                input_field_placeholder=placeholder,
            ),
        )


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

# (matcher, handler) 列表，按注册顺序匹配（短路）
_HANDLERS: list[tuple[Callable[[str], bool], Callable]] = []


def register(matcher: Callable[[str], bool]) -> Callable:
    """
    装饰器：将一个 async handler 注册到全局回调路由表。

    handler 签名：async def handler(ctx: CallbackContext) -> None

    参数
    ----
    matcher:
        接收 callback_data 字符串，返回 bool 的谓词函数。
        第一个匹配的 handler 胜出（短路）。

    示例
    ----
    @register(lambda d: d.startswith("download_"))
    async def _handle_download(ctx: CallbackContext) -> None:
        await ctx.platform_ctx.edit("开始下载…")
    """
    def decorator(func: Callable) -> Callable:
        _HANDLERS.append((matcher, func))
        logger.debug(
            "callback_bus: registered handler %s for matcher %s",
            func.__name__, matcher,
        )
        return func
    return decorator


def clear_handlers() -> None:
    """清空路由表（仅供测试使用）。"""
    _HANDLERS.clear()


# ---------------------------------------------------------------------------
# PTB entry point  （挂载到 app.add_handler 的唯一入口）
# ---------------------------------------------------------------------------

async def handle_callback(update: Any, context: Any) -> None:
    """
    统一的 PTB CallbackQueryHandler 入口。

    在 DownloaderModule.setup() 中挂载一次：
        app.add_handler(CallbackQueryHandler(handle_callback))

    此函数：
    1. 从 PTB update 中提取 CallbackQuery。
    2. 包裹成 TelegramCallbackContext（所有 telegram.* 细节止步于此）。
    3. 按注册顺序匹配 handler，短路执行第一个匹配项。
    4. 无匹配时静默 ack，避免 Telegram 客户端超时提示。
    """
    query = update.callback_query
    if not query:
        return

    ctx = TelegramCallbackContext(query, context)
    data = ctx.data

    for matcher, handler in _HANDLERS:
        try:
            if matcher(data):
                await handler(ctx)
                return
        except Exception as exc:
            logger.error(
                "callback_bus: handler %s raised for data=%r: %s",
                handler.__name__, data, exc, exc_info=True,
            )
            try:
                await ctx.answer_alert("❌ 处理出错，请重试。")
            except Exception:
                pass
            return

    # 无 handler 匹配 — 静默 ack
    await ctx.answer()
    logger.warning("callback_bus: unhandled callback_data=%r", data)
