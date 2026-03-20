# modules/downloader/__init__.py
"""
DownloaderModule — zero top-level telegram.* imports.

``setup()`` now receives a ``HandlerRegistrar`` and calls only its
platform-agnostic methods.  PTB-specific wiring lives entirely in
``PtbHandlerRegistrar`` (infra layer).
"""
from __future__ import annotations

from core.registrar import HandlerRegistrar


class DownloaderModule:
    name = "downloader"

    def setup(self, registrar: HandlerRegistrar) -> None:
        # ── Trigger side-effect @register decorators ──────────────────────
        # Importing these modules causes their module-level @register and
        # @command_handler decorators to fire, populating:
        #   • core.callback_bus._HANDLERS  (inline_actions, admin tasks)
        #   • core.handler_registry.registry  (all @command_handler entries)
        import modules.downloader.handlers.inline_actions   # noqa: F401
        import handlers.admin.tasks                         # noqa: F401
        # modules/downloader/__init__.py — setup 方法内追加

        from handlers.user.bind import handle_bind_command

        registrar.apply_command_registry()   # 已有这行，在它之后追加：

        # 手动注册 /bind（不走 @command_handler 装饰器）
        registrar.register_command("bind", handle_bind_command)
        
        from modules.downloader.handlers.message_parser import handle_link
        from handlers.admin.cookies import handle_cookie_file

        # ── Flush @command_handler registry ───────────────────────────────
        # Writes every queued CommandEntry into the registrar (which maps
        # them to PTB CommandHandlers when running under Telegram).
        registrar.apply_command_registry()

        # ── Unified callback bus ───────────────────────────────────────────
        # All modules' @register callbacks are routed through a single PTB
        # CallbackQueryHandler.  BillingModule's callbacks arrive here too.
        registrar.register_callback_query_bus()

        # ── Message handlers ───────────────────────────────────────────────
        registrar.register_message(handle_link, "TEXT")
        registrar.register_message(handle_cookie_file, "COOKIE")

    def get_user_commands(self) -> list[str]:
        return ["start", "cancel", "help", "history", "myid", "lang", "tasks", "bind"]

    def get_admin_commands(self) -> list[str]:
        return [
            "stats", "allow", "block", "users", "broadcast", "userhistory",
            "rateinfo", "setrate", "cleanup", "status", "queue", "storage",
            "failed", "cookie", "refresh", "admcancel", "settier", "setdisk", "report",
        ]
