"""
shared/services/platform_context.py
─────────────────────────────────────
Platform-agnostic context object that handler logic operates on.

Design
------
Instead of handlers importing telegram.Update directly, they receive a
PlatformContext, which exposes only the actions every platform supports:

    • user_id / username / display_name  — who sent the request
    • args                               — command arguments (tokenised)
    • send(text)                         — reply with plain text
    • send_keyboard(text, buttons)       — reply with an inline keyboard
    • send_markdown(text)                — reply with Markdown-formatted text
    • edit(text)                         — edit the originating message in-place
    • bot_send(chat_id, text)            — proactive message to any chat_id
                                           (admin broadcasts, etc.)

Everything platform-specific (ParseMode, InlineKeyboardMarkup, file_id
caching, etc.) lives in the TelegramContext subclass or other adapters —
never in handler business logic.

Compatibility
-------------
All existing PTB handlers continue to work unchanged — they still accept
(Update, CallbackContext) and may still call update.message.reply_text
directly. The adapter is opt-in: refactor one handler at a time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


# ---------------------------------------------------------------------------
# Keyboard building — minimal, platform-neutral representation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class KeyboardButton:
    """A single inline button."""
    label: str
    callback_data: str


# A keyboard is a list of rows; each row is a list of buttons.
KeyboardLayout = list[list[KeyboardButton]]


def btn(label: str, data: str) -> KeyboardButton:
    """Shorthand constructor used in handlers."""
    return KeyboardButton(label=label, callback_data=data)


# ---------------------------------------------------------------------------
# Platform context — abstract base
# ---------------------------------------------------------------------------

class PlatformContext(ABC):
    """
    Abstract platform context passed into handler business logic.

    Each method corresponds to one user-visible action.  Concrete
    subclasses implement them for a specific platform.

    Attributes
    ----------
    user_id:
        Numeric identifier of the requesting user.
    username:
        Platform handle (e.g. "@alice"), or empty string if not available.
    display_name:
        Human-readable full name, or a fallback string.
    args:
        Tokenised command arguments (everything after the command word).
    """

    user_id: int
    username: str
    display_name: str
    args: list[str]

    # ------------------------------------------------------------------ output

    @abstractmethod
    async def send(self, text: str) -> None:
        """Send a plain-text reply."""
        ...

    @abstractmethod
    async def send_markdown(self, text: str) -> None:
        """Send a Markdown-formatted reply."""
        ...

    @abstractmethod
    async def send_keyboard(
        self,
        text: str,
        buttons: KeyboardLayout,
    ) -> None:
        """Send a reply with an inline keyboard.

        Parameters
        ----------
        text:
            Message body.
        buttons:
            2-D list of ``KeyboardButton`` rows.
        """
        ...

    @abstractmethod
    async def edit(self, text: str) -> None:
        """Edit the current message in-place (e.g. update a status line)."""
        ...

    @abstractmethod
    async def bot_send(self, chat_id: int, text: str) -> None:
        """Send a message to an arbitrary chat_id (for admin broadcasts etc.)."""
        ...


# ---------------------------------------------------------------------------
# Telegram implementation
# ---------------------------------------------------------------------------

class TelegramContext(PlatformContext):
    """
    PlatformContext built from a PTB (Update, CallbackContext) pair.

    Supports both message commands and callback queries via the two
    factory classmethods.

    All telegram.* imports are confined to this class.
    """

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

    # ------------------------------------------------------------------ factories

    @classmethod
    def from_message(cls, update: Any, context: Any) -> "TelegramContext":
        """Construct from a PTB Update carrying a Message."""
        from telegram.constants import ParseMode as _PM  # noqa: F401 — type hint only

        msg = update.message
        user = msg.from_user
        args: list[str] = list(context.args or [])

        async def _reply(text: str, **kw: Any) -> None:
            await msg.reply_text(text, **kw)

        async def _edit(text: str, **kw: Any) -> None:
            # Message edits are not meaningful for incoming commands;
            # fall back to a new reply so callers never need to branch.
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
        """Construct from a PTB CallbackQuery."""
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

    # ------------------------------------------------------------------ PlatformContext

    async def send(self, text: str) -> None:
        await self._reply(text)

    async def send_markdown(self, text: str) -> None:
        await self._reply(text, parse_mode="Markdown")

    async def send_keyboard(self, text: str, buttons: KeyboardLayout) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        tg_keyboard = [
            [InlineKeyboardButton(b.label, callback_data=b.callback_data) for b in row]
            for row in buttons
        ]
        await self._reply(text, reply_markup=InlineKeyboardMarkup(tg_keyboard))

    async def edit(self, text: str) -> None:
        await self._edit(text)

    async def bot_send(self, chat_id: int, text: str) -> None:
        await self._bot_send(chat_id, text)
