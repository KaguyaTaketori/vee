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
        import modules.billing.handlers.bill_callbacks      # noqa: F401

        from modules.billing.handlers.bill_handler import (
            handle_bill_command,
            handle_bill_text,
            handle_bill_photo,
            handle_jz_command,
        )
        from modules.billing.handlers.bill_callbacks import handle_bill_edit_reply
        from modules.billing.handlers.mybills_handler import handle_mybills_command

        # ── Register handlers via the platform-agnostic interface ──────────
        registrar.register_command("bill", handle_bill_command)
        registrar.register_command("jz", handle_jz_command)
        registrar.register_command("mybills", handle_mybills_command)
        registrar.register_message(handle_bill_photo, "PHOTO")
        registrar.register_message(handle_bill_edit_reply, "TEXT_REPLY", group=1)
        registrar.register_message(handle_bill_text, "BILL_TEXT", group=0)

    async def init_db(self) -> None:
        from modules.billing.database.bills import init_bills_table
        await init_bills_table()

    def get_user_commands(self) -> list[str]:
        return ["bill", "jz", "mybills"]

    def get_admin_commands(self) -> list[str]:
        return []
