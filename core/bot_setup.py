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

async def set_bot_commands(app: Application, modules:list):
    user_cmds = []
    admin_cmds = []
    for module in modules:
        user_cmds.extend(module.get_user_commands())
        admin_cmds.extend(module.get_admin_commands())

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
            logger.debug("Set user commands for lang=%s", lang_code)
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
            logger.warning("set_bot_commands partial failure: %s", err)
    else:
        logger.info(
            "Bot commands set for %s languages and %s admins",
            len(LANGUAGES),
            len(ADMIN_IDS)
        )
