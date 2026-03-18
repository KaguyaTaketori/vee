# modules/downloader/__init__.py
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, filters

from core.callback_bus import handle_callback   # 统一回调入口，来自 core 层
from core.filters import CookieFilter
from core.handler_registry import registry
from modules.downloader.integrations.ptb_adapter import PtbCommandRegistrar
from config import ADMIN_IDS


class DownloaderModule:
    name = "downloader"

    def setup(self, app: Application) -> None:
        # 触发 @register 副作用注册（downloader 侧的所有回调 handler）
        import modules.downloader.handlers.inline_actions   # noqa: F401
        import handlers.admin.tasks                         # noqa: F401

        from modules.downloader.handlers.message_parser import handle_link
        from handlers.admin.cookies import handle_cookie_file

        registry.apply(PtbCommandRegistrar(app, admin_ids=frozenset(ADMIN_IDS)))

        # 挂载统一回调入口（billing 的回调也会通过 core.callback_bus 路由到这里）
        app.add_handler(CallbackQueryHandler(handle_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
        app.add_handler(MessageHandler(filters.Document.ALL & CookieFilter(), handle_cookie_file))

    def get_user_commands(self) -> list[str]:
        return ["start", "cancel", "help", "history", "myid", "lang", "tasks"]

    def get_admin_commands(self) -> list[str]:
        return [
            "stats", "allow", "block", "users", "broadcast", "userhistory",
            "rateinfo", "setrate", "cleanup", "status", "queue", "storage",
            "failed", "cookie", "refresh", "admcancel", "settier", "setdisk", "report",
        ]
