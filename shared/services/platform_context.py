# shared/services/platform_context.py
"""
shared/services/platform_context.py
─────────────────────────────────────
Platform-agnostic context object that handler logic operates on.

Methods
-------
send(text)                  — plain-text reply
send_markdown(text)         — Markdown reply (Telegram parse_mode=Markdown)
send_markdown_v2(text)      — MarkdownV2 reply (for escape_markdown content)
send_keyboard(text, btns)   — reply with an inline keyboard
edit(text)                  — edit current message, plain text
edit_keyboard(text, btns)   — edit current message, with new inline keyboard
bot_send(chat_id, text)     — proactive send to any chat_id
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Awaitable


# ---------------------------------------------------------------------------
# Keyboard primitives
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class KeyboardButton:
    """A single inline button."""
    label: str
    callback_data: str


KeyboardLayout = list[list[KeyboardButton]]


def btn(label: str, data: str) -> KeyboardButton:
    return KeyboardButton(label=label, callback_data=data)


# ---------------------------------------------------------------------------
# PlatformContext ABC
# ---------------------------------------------------------------------------

class PlatformContext(ABC):
    user_id: int
    username: str
    display_name: str
    args: list[str]

    @abstractmethod
    async def send(self, text: str) -> None: ...

    @abstractmethod
    async def send_markdown(self, text: str) -> None: ...

    async def send_markdown_v2(self, text: str) -> None:
        """MarkdownV2 reply — default falls back to send_markdown.
        Override in TelegramContext to use parse_mode=MarkdownV2.
        """
        await self.send_markdown(text)

    @abstractmethod
    async def send_keyboard(self, text: str, buttons: KeyboardLayout) -> None: ...

    async def edit_keyboard(self, text: str, buttons: KeyboardLayout) -> None:
        """Edit the current message replacing it with text + new keyboard.

        Default implementation falls back to send_keyboard (sends a new
        message).  TelegramContext overrides this with edit_message_text +
        reply_markup so the existing message is mutated in-place.
        """
        await self.send_keyboard(text, buttons)

    @abstractmethod
    async def edit(self, text: str) -> None: ...

    @abstractmethod
    async def bot_send(self, chat_id: int, text: str) -> None: ...


# ---------------------------------------------------------------------------
# TelegramContext
# ---------------------------------------------------------------------------

class TelegramContext(PlatformContext):
    """PTB implementation.  All telegram.* imports confined here."""

    def __init__(
        self,
        user_id: int,
        username: str,
        display_name: str,
        args: list[str],
        _reply_fn: Callable[..., Awaitable[Any]],
        _edit_fn: Callable[..., Awaitable[Any]],
        _bot_send_fn: Callable[..., Awaitable[Any]],
    ) -> None:
        self.user_id = user_id
        self.username = username
        self.display_name = display_name
        self.args = args
        self._reply = _reply_fn
        self._edit = _edit_fn
        self._bot_send = _bot_send_fn

    # ── factories ──────────────────────────────────────────────────────────

    @classmethod
    def from_message(cls, update: Any, context: Any) -> "TelegramContext":
        from telegram.constants import ParseMode as _PM  # noqa: F401

        msg = update.message
        user = msg.from_user
        args: list[str] = list(context.args or [])

        async def _reply(text: str, **kw: Any) -> None:
            await msg.reply_text(text, **kw)

        async def _edit(text: str, **kw: Any) -> None:
            await msg.reply_text(text, **kw)

        async def _bot_send(chat_id: int, text: str) -> None:
            await context.bot.send_message(chat_id=chat_id, text=text)

        return cls(
            user_id=user.id,
            username=user.username or "",
            display_name=f"{user.first_name} {user.last_name or ''}".strip(),
            args=args,
            _reply_fn=_reply,
            _edit_fn=_edit,
            _bot_send_fn=_bot_send,
        )

    @classmethod
    def from_callback_query(cls, query: Any, context: Any) -> "TelegramContext":
        user = query.from_user

        async def _reply(text: str, **kw: Any) -> None:
            await query.message.reply_text(text, **kw)

        async def _edit(text: str, **kw: Any) -> None:
            await query.edit_message_text(text, **kw)

        async def _bot_send(chat_id: int, text: str) -> None:
            await context.bot.send_message(chat_id=chat_id, text=text)

        return cls(
            user_id=user.id,
            username=user.username or "",
            display_name=f"{user.first_name} {user.last_name or ''}".strip(),
            args=[],
            _reply_fn=_reply,
            _edit_fn=_edit,
            _bot_send_fn=_bot_send,
        )

    # ── PlatformContext impl ───────────────────────────────────────────────

    async def send(self, text: str) -> None:
        await self._reply(text)

    async def send_markdown(self, text: str) -> None:
        await self._reply(text, parse_mode="Markdown")

    async def send_markdown_v2(self, text: str) -> None:
        await self._reply(text, parse_mode="MarkdownV2")

    async def send_keyboard(self, text: str, buttons: KeyboardLayout) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(b.label, callback_data=b.callback_data) for b in row]
            for row in buttons
        ])
        await self._reply(text, reply_markup=markup)

    async def edit_keyboard(self, text: str, buttons: KeyboardLayout) -> None:
        """Edit the current message in-place (callback-query context)."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(b.label, callback_data=b.callback_data) for b in row]
            for row in buttons
        ])
        await self._edit(text, reply_markup=markup)

    async def edit(self, text: str) -> None:
        await self._edit(text)

    async def bot_send(self, chat_id: int, text: str) -> None:
        await self._bot_send(chat_id, text)
