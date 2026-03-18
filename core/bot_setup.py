import logging
from telegram import Update, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from telegram.ext import Application
from config import ADMIN_IDS
from utils.i18n import LANGUAGES, get_command_description

logger = logging.getLogger(__name__)


def _build_commands(names: list[str], scope: str, lang: str) -> list[BotCommand]:
    return [
        BotCommand(
            command=name,
            description=get_command_description(name, scope, lang)
        )
        for name in names
    ]


async def set_bot_commands(app: Application, modules: list):
    # 从各模块收集命令名，保持注册顺序
    user_cmd_names: list[str] = []
    admin_cmd_names: list[str] = []
    for module in modules:
        for cmd in module.get_user_commands():
            if cmd not in user_cmd_names:
                user_cmd_names.append(cmd)
        for cmd in module.get_admin_commands():
            if cmd not in admin_cmd_names:
                admin_cmd_names.append(cmd)

    bot = app.bot
    errors = []

    # 普通用户命令（所有语言）
    for lang_code in LANGUAGES:
        try:
            await bot.set_my_commands(
                _build_commands(user_cmd_names, "user", lang_code),
                scope=BotCommandScopeDefault(),
                language_code=lang_code,
            )
        except Exception as e:
            errors.append(f"user commands lang={lang_code}: {e}")

    # 普通用户命令（fallback 无语言）
    try:
        await bot.set_my_commands(
            _build_commands(user_cmd_names, "user", "en"),
            scope=BotCommandScopeDefault(),
        )
    except Exception as e:
        errors.append(f"user commands fallback: {e}")

    # 管理员命令（所有语言 + fallback）
    all_admin_names = user_cmd_names + admin_cmd_names
    if ADMIN_IDS:
        for admin_id in ADMIN_IDS:
            for lang_code in LANGUAGES:
                try:
                    await bot.set_my_commands(
                        _build_commands(all_admin_names, "admin", lang_code),
                        scope=BotCommandScopeChat(chat_id=admin_id),
                        language_code=lang_code,
                    )
                except Exception as e:
                    errors.append(f"admin {admin_id} lang={lang_code}: {e}")

            try:
                await bot.set_my_commands(
                    _build_commands(all_admin_names, "admin", "en"),
                    scope=BotCommandScopeChat(chat_id=admin_id),
                )
            except Exception as e:
                errors.append(f"admin {admin_id} fallback: {e}")

    if errors:
        for err in errors:
            logger.warning("set_bot_commands partial failure: %s", err)
    else:
        logger.info(
            "Bot commands set: %d user, %d admin, %d languages, %d admins",
            len(user_cmd_names),
            len(admin_cmd_names),
            len(LANGUAGES),
            len(ADMIN_IDS),
        )
