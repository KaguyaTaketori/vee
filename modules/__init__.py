# modules/__init__.py
from typing import Protocol
from telegram.ext import Application

class BotModule(Protocol):
    name: str

    def setup(self, app: Application) -> None:
        """注册所有 handler 到 app"""
        ...

    def get_user_commands(self) -> list[str]:
        """该模块提供的用户命令名列表"""
        return []

    def get_admin_commands(self) -> list[str]:
        """该模块提供的管理员命令名列表"""
        return []

    async def init_db(self) -> None:
        """初始化本模块相关的数据库表（可选）"""
        ...
