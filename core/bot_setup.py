import logging
from telegram import Update, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from telegram.ext import Application
from config import ADMIN_IDS
from utils.i18n import LANGUAGES, get_command_description

logger = logging.getLogger(__name__)


_USER_COMMAND_NAMES = [
    "start",
    "cancel",
    "help",
    "history",
    "myid",
    "lang",
    "tasks",
]

_ADMIN_COMMAND_NAMES = [
    "stats",
    "allow",
    "block",
    "users",
    "broadcast",
    "userhistory",
    "rateinfo",
    "setrate",
    "cleanup",
    "status",
    "queue",
    "storage",
    "failed",
    "cookie",
    "refresh",
    "admcancel",
    "settier",
    "setdisk",
    "report",
]

def _build_commands(names: list[str], scope: str, lang: str) -> list[BotCommand]:
    return [
        BotCommand(
            command=name,
            description=get_command_description(name, scope, lang)
        )
        for name in names
    ]

async def set_bot_commands(app: Application):
    bot = app.bot
    errors = []

    for lang_code in LANGUAGES:
        try:
            commands = _build_commands(_USER_COMMAND_NAMES, "user", lang_code)
            await bot.set_my_commands(
                commands,
                scope=BotCommandScopeDefault(),
                language_code=lang_code,
            )
            logger.debug(f"Set user commands for lang={lang_code}")
        except Exception as e:
            errors.append(f"user commands lang={lang_code}: {e}")

    try:
        await bot.set_my_commands(
            _build_commands(_USER_COMMAND_NAMES, "user", "en"),
            scope=BotCommandScopeDefault(),
        )
    except Exception as e:
        errors.append(f"user commands fallback: {e}")

    if ADMIN_IDS:
        for admin_id in ADMIN_IDS:
            for lang_code in LANGUAGES:
                try:
                    commands = _build_commands(
                        _USER_COMMAND_NAMES + _ADMIN_COMMAND_NAMES,
                        "admin",
                        lang_code,
                    )
                    await bot.set_my_commands(
                        commands,
                        scope=BotCommandScopeChat(chat_id=admin_id),
                        language_code=lang_code,
                    )
                except Exception as e:
                    errors.append(f"admin {admin_id} lang={lang_code}: {e}")

            try:
                await bot.set_my_commands(
                    _build_commands(
                        _USER_COMMAND_NAMES + _ADMIN_COMMAND_NAMES,
                        "admin",
                        "en",
                    ),
                    scope=BotCommandScopeChat(chat_id=admin_id),
                )
            except Exception as e:
                errors.append(f"admin {admin_id} fallback: {e}")

    if errors:
        for err in errors:
            logger.warning(f"set_bot_commands partial failure: {err}")
    else:
        logger.info(
            f"Bot commands set for {len(LANGUAGES)} languages"
            f" and {len(ADMIN_IDS)} admins"
        )
