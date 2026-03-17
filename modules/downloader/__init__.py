from telegram.ext import Application, MessageHandler, filters
from core.filters import CookieFilter
from core.handler_registry import registry
from integrations.ptb_adapter import PtbCommandRegistrar
from config import ADMIN_IDS

class DownloaderModule:
    name = "downloader"

    def setup(self, app: Application) -> None:
        import modules.downloader.handlers.message_parser
        import handlers.admin.tasks
        from modules.downloader.handlers.message_parser import handle_link
        from modules.downloader.handlers.inline_actions import handle_callback
        from handlers.admin.cookies import handle_cookie_file
        from telegram.ext import CallbackQueryHandler

        registry.apply(PtbCommandRegistrar(app, admin_ids=frozenset(ADMIN_IDS)))
        app.add_handler(CallbackQueryHandler(handle_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
        app.add_handler(MessageHandler(filters.Document.ALL & CookieFilter(), handle_cookie_file))

    def get_user_commands(self) -> list[str]:
        return ["start", "cancel", "help", "history", "myid", "lang", "tasks"]

    def get_admin_commands(self) -> list[str]:
        return ["stats", "allow", "block", "users", "broadcast", "userhistory",
                "rateinfo", "setrate", "cleanup", "status", "queue", "storage",
                "failed", "cookie", "refresh", "admcancel", "settier", "setdisk", "report"]
