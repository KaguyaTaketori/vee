"""
声明式 Handler 注册系统。

用法（在 handler 文件中）：
    from core.handler_registry import command_handler

    @command_handler("stats", admin_only=True)
    async def stats_command(update, context): ...

    @command_handler("start")
    async def start_command(update, context): ...

在 main.py 中只需：
    from core.handler_registry import registry
    registry.apply(app)

设计原则：
- 注册元数据与实现共存于同一文件，增删命令只改一处。
- main.py 不再需要 import 任何具体 handler 函数。
- 顺序：用户命令先注册，管理员命令后注册。
- admin_only=True 的 handler 在 ADMIN_IDS 为空时自动跳过注册。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Any

from telegram.ext import Application, CommandHandler

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 注册表数据结构
# ---------------------------------------------------------------------------

@dataclass
class CommandEntry:
    command: str
    handler: Callable
    admin_only: bool = False
    extra_kwargs: dict = field(default_factory=dict)


class HandlerRegistry:
    """全局注册表，收集所有 @command_handler 声明。"""

    def __init__(self) -> None:
        self._entries: list[CommandEntry] = []

    def add(self, entry: CommandEntry) -> None:
        self._entries.append(entry)
        logger.debug(
            "HandlerRegistry: registered /%s (admin=%s)",
            entry.command, entry.admin_only,
        )

    def apply(self, app: Application) -> None:
        """将所有已注册的命令挂载到 PTB Application。

        需要在 ADMIN_IDS 已加载之后调用（main() 里或 post_init 内均可）。
        """
        from config import ADMIN_IDS
        from core.filters import AdminFilter

        user_entries  = [e for e in self._entries if not e.admin_only]
        admin_entries = [e for e in self._entries if e.admin_only]

        for entry in user_entries:
            app.add_handler(
                CommandHandler(entry.command, entry.handler, **entry.extra_kwargs)
            )
            logger.debug("Registered user command: /%s", entry.command)

        if ADMIN_IDS:
            for entry in admin_entries:
                app.add_handler(
                    CommandHandler(
                        entry.command,
                        entry.handler,
                        filters=AdminFilter(),
                        **entry.extra_kwargs,
                    )
                )
                logger.debug("Registered admin command: /%s", entry.command)

    @property
    def all_commands(self) -> list[CommandEntry]:
        return list(self._entries)


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------

registry = HandlerRegistry()


# ---------------------------------------------------------------------------
# 装饰器
# ---------------------------------------------------------------------------

def command_handler(command: str, *, admin_only: bool = False, **kwargs: Any):
    """将函数声明为某个 Telegram 命令的处理器。

    Args:
        command:    命令名，不含斜杠（如 "start"、"stats"）。
        admin_only: 为 True 时，注册时自动附加 AdminFilter，且 ADMIN_IDS
                    为空时跳过注册。
        **kwargs:   透传给 CommandHandler() 的额外参数。

    Example::

        @command_handler("lang")
        async def lang_command(update, context): ...

        @command_handler("stats", admin_only=True)
        async def stats_command(update, context): ...
    """
    def decorator(func: Callable) -> Callable:
        registry.add(CommandEntry(
            command=command,
            handler=func,
            admin_only=admin_only,
            extra_kwargs=kwargs,
        ))
        return func  # 函数本身不做任何修改，仍可单独调用/测试
    return decorator
