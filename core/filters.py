from telegram.ext import filters
from config import ADMIN_IDS


class AdminFilter(filters.BaseFilter):
    def filter(self, message):
        return bool(ADMIN_IDS) and message.from_user.id in ADMIN_IDS


class CookieFilter(AdminFilter):
    def filter(self, message):
        return message.document is not None and super().filter(message)
