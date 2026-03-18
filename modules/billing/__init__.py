# modules/billing/__init__.py
"""
BillingModule — zero top-level telegram.* imports.

``setup()`` receives a ``HandlerRegistrar`` and uses only its
platform-agnostic interface.
"""
from __future__ import annotations

from core.registrar import HandlerRegistrar


class BillingModule:
    name = "billing"

    def setup(self, registrar: HandlerRegistrar) -> None:
        # ── Trigger @register side-effects for billing callbacks ───────────
        # bill_confirm / bill_edit / bill_cancel handlers are registered into
        # core.callback_bus when this module is imported.
        import modules.billing.handlers.bill_callbacks      # noqa: F401

        from modules.billing.handlers.bill_handler import (
            handle_bill_command,
            handle_bill_photo,
        )
        from modules.billing.handlers.bill_callbacks import handle_bill_edit_reply

        # ── Register handlers via the platform-agnostic interface ──────────
        registrar.register_command("bill", handle_bill_command)
        registrar.register_message(handle_bill_photo, "PHOTO")
        registrar.register_message(handle_bill_edit_reply, "TEXT_REPLY", group=1)

    async def init_db(self) -> None:
        from modules.billing.database.bills import init_bills_table
        await init_bills_table()

    def get_user_commands(self) -> list[str]:
        return ["bill"]

    def get_admin_commands(self) -> list[str]:
        return []
