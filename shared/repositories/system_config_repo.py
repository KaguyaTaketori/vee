# shared/repositories/system_config_repo.py
"""
SystemConfigRepository
======================
负责 system_configs 表的全部 SQL 操作。

使用示例
--------
    repo = SystemConfigRepository()

    # 读取单个配置
    value = await repo.get("default_permissions")  # → '["bot_text","bot_receipt"]'

    # 读取并解析 JSON
    perms = await repo.get_json("default_permissions")  # → ["bot_text", "bot_receipt"]

    # 写入
    await repo.set("registration_open", "false", updated_by=admin_user_id)

    # 批量读取（常用于系统启动缓存）
    all_configs = await repo.get_all()   # → {"key": "value", ...}
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from shared.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class SystemConfigRepository(BaseRepository):

    async def get(self, key: str) -> Optional[str]:
        """返回 config_value 字符串，key 不存在则返回 None。"""
        async with self._db() as db:
            async with db.execute(
                "SELECT config_value FROM system_configs WHERE config_key = ?",
                (key,),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    async def get_json(self, key: str, default: Any = None) -> Any:
        """读取并 JSON 反序列化，解析失败或不存在时返回 default。"""
        raw = await self.get(key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("system_config[%s] JSON 解析失败: %s", key, e)
            return default

    async def get_bool(self, key: str, default: bool = True) -> bool:
        """读取布尔类型配置（"true"/"false" 字符串）。"""
        raw = await self.get(key)
        if raw is None:
            return default
        return raw.strip().lower() == "true"

    async def get_int(self, key: str, default: int = 0) -> int:
        """读取整数类型配置。"""
        raw = await self.get(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except (ValueError, TypeError):
            return default

    async def get_all(self) -> dict[str, str]:
        """返回全部配置项，格式 {key: value}。"""
        async with self._db() as db:
            async with db.execute(
                "SELECT config_key, config_value FROM system_configs"
            ) as cur:
                return {row[0]: row[1] for row in await cur.fetchall()}

    async def get_all_with_meta(self) -> list[dict]:
        """返回全部配置项（含 description、updated_at），管理 API 使用。"""
        async with self._db() as db:
            async with db.execute(
                """
                SELECT config_key, config_value, description,
                       updated_by, updated_at
                FROM system_configs
                ORDER BY config_key
                """
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def set(
        self,
        key: str,
        value: str,
        *,
        description: Optional[str] = None,
        updated_by: Optional[int] = None,
    ) -> None:
        """
        新增或更新配置项（upsert）。
        description 为 None 时保留原有描述。
        """
        now = int(time.time())
        async with self._db() as db:
            if description is not None:
                await db.execute(
                    """
                    INSERT INTO system_configs
                        (config_key, config_value, description, updated_by, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(config_key) DO UPDATE SET
                        config_value = excluded.config_value,
                        description  = excluded.description,
                        updated_by   = excluded.updated_by,
                        updated_at   = excluded.updated_at
                    """,
                    (key, value, description, updated_by, now),
                )
            else:
                await db.execute(
                    """
                    INSERT INTO system_configs
                        (config_key, config_value, updated_by, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(config_key) DO UPDATE SET
                        config_value = excluded.config_value,
                        updated_by   = excluded.updated_by,
                        updated_at   = excluded.updated_at
                    """,
                    (key, value, updated_by, now),
                )
            await db.commit()
        logger.info("system_config[%s] 已更新 by user_id=%s", key, updated_by)

    async def set_json(
        self,
        key: str,
        value: Any,
        *,
        description: Optional[str] = None,
        updated_by: Optional[int] = None,
    ) -> None:
        """序列化后写入。"""
        await self.set(
            key,
            json.dumps(value, ensure_ascii=False),
            description=description,
            updated_by=updated_by,
        )

    async def delete(self, key: str) -> bool:
        """删除配置项，返回是否存在并被删除。"""
        async with self._db() as db:
            cursor = await db.execute(
                "DELETE FROM system_configs WHERE config_key = ?", (key,)
            )
            await db.commit()
            return cursor.rowcount > 0
