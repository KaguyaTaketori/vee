from telegram.ext import Application, MessageHandler, CommandHandler, filters

class BillingModule:
    name = "billing"

    def setup(self, app: Application) -> None:
        from modules.billing.handlers.bill_handler import handle_bill_text, handle_bill_photo, handle_bill_command
        from modules.billing.handlers.bill_callbacks import handle_bill_edit_reply
        import modules.billing.handlers

        app.add_handler(CommandHandler("bill", handle_bill_command))
        app.add_handler(MessageHandler(filters.PHOTO, handle_bill_photo))
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_bill_text),
            group=1,
        )
        app.add_handler(
            MessageHandler(filters.TEXT & filters.REPLY & ~filters.COMMAND, handle_bill_edit_reply),
            group=1,
        )

    def get_user_commands(self) -> list[str]:
        return ["bill", "jz"]

    def get_admin_commands(self):
        return []

    async def init_db(self) -> None:
        from modules.billing.database.bills import init_bills_table
        await init_bills_table()
