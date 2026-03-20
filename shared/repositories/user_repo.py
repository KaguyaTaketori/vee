# shared/repositories/user_repo.py
"""
统一 UserRepository：覆盖原 UserRepository + AppUserRepository 全部功能。
主键为 users.id（自增），tg_user_id / email 均为查询入口。
"""
from __future__ import annotations

import hashlib
import secrets
import time
import logging
from typing import Optional

from shared.repositories.base import BaseRepository

logger = logging.getLogger(__name__)

_REFRESH_TOKEN_TTL   = 30 * 24 * 3600
_VERIFY_CODE_TTL     = 10 * 60
_BIND_CODE_TTL       = 10 * 60
_AI_QUOTA_RESET_DAYS = 30


class UserRepository(BaseRepository):

    # ── 查找 ─────────────────────────────────────────────────────────────

    async def get_by_id(self, user_id: int) -> Optional[dict]:
        async with self._db() as db:
            async with db.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_by_tg_id(self, tg_user_id: int) -> Optional[dict]:
        async with self._db() as db:
            async with db.execute(
                "SELECT * FROM users WHERE tg_user_id = ?", (tg_user_id,)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_by_email(self, email: str) -> Optional[dict]:
        async with self._db() as db:
            async with db.execute(
                "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_by_app_username(self, username: str) -> Optional[dict]:
        async with self._db() as db:
            async with db.execute(
                "SELECT * FROM users WHERE app_username = ?", (username.strip(),)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_by_identifier(self, identifier: str) -> Optional[dict]:
        """邮箱或 app_username 均可登录"""
        if "@" in identifier:
            return await self.get_by_email(identifier)
        return await self.get_by_app_username(identifier)

    async def get_lang(self, user_id: int) -> str:
        async with self._db() as db:
            async with db.execute(
                "SELECT lang FROM users WHERE id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row and row[0] else "en"

    async def get_all(self) -> list[dict]:
        async with self._db() as db:
            async with db.execute("SELECT * FROM users") as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def count_all(self) -> int:
        async with self._db() as db:
            async with db.execute("SELECT COUNT(*) FROM users") as cur:
                return (await cur.fetchone())[0]

    # ── TG 用户 upsert（Bot 侧入口）─────────────────────────────────────

    async def upsert_tg_user(
        self,
        tg_user_id: int,
        *,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        lang: str = "en",
    ) -> int:
        """
        创建或更新 TG 用户，返回 users.id。
        首次创建时自动从 system_configs 读取并写入默认功能权限。
        """
        now = int(time.time())
        async with self._db() as db:
            async with db.execute(
                "SELECT id FROM users WHERE tg_user_id = ?", (tg_user_id,)
            ) as cur:
                row = await cur.fetchone()
 
            if row:
                # 已存在：仅更新活跃信息
                await db.execute(
                    """
                    UPDATE users SET
                        tg_username = ?, tg_first_name = ?, tg_last_name = ?,
                        last_seen = ?, updated_at = ?
                    WHERE tg_user_id = ?
                    """,
                    (username, first_name, last_name, now, now, tg_user_id),
                )
                await db.commit()
                return row[0]
 
            else:
                # 首次创建：写入基础字段
                cursor = await db.execute(
                    """
                    INSERT INTO users (
                        tg_user_id, tg_username, tg_first_name, tg_last_name,
                        lang, is_active, ai_quota_monthly, ai_quota_used,
                        ai_quota_reset_at, created_at, updated_at, last_seen
                    ) VALUES (?, ?, ?, ?, ?, 1, 100, 0, ?, ?, ?, ?)
                    """,
                    (
                        tg_user_id, username, first_name, last_name, lang,
                        now + _AI_QUOTA_RESET_DAYS * 86400,
                        now, now, now,
                    ),
                )
                await db.commit()
                new_id = cursor.lastrowid
 
        # 首次创建后，在锁外读取默认权限并写入
        # （set_permissions 内部会重新开连接，避免嵌套事务）
        await self._assign_default_permissions(new_id)
        return new_id
 
    async def _assign_default_permissions(self, user_id: int) -> None:
        """
        从 system_configs 读取 default_permissions 并写入新用户。
        读取失败时静默使用 fallback，不阻断注册/首次登录流程。
        """
        import json as _json
        fallback = ["bot_text", "bot_receipt"]
        try:
            from shared.repositories.system_config_repo import SystemConfigRepository
            raw = await SystemConfigRepository().get("default_permissions")
            if raw:
                parsed = _json.loads(raw)
                perms  = parsed if isinstance(parsed, list) else fallback
            else:
                perms = fallback
        except Exception as e:
            logging.getLogger(__name__).warning(
                "_assign_default_permissions 读取配置失败，使用默认值: %s", e
            )
            perms = fallback
 
        await self.set_permissions(user_id, perms)
        logging.getLogger(__name__).info(
            "新用户 id=%s 已分配默认权限: %s", user_id, perms
        )
    # ── App 用户注册（App 侧入口）────────────────────────────────────────

    async def create_app_user(
        self,
        *,
        app_username: str,
        email: str,
        password_hash: str,
        display_name: str = "",
        ai_quota_monthly: int = 100,
    ) -> int:
        now = int(time.time())
        async with self._db() as db:
            cursor = await db.execute(
                """
                INSERT INTO users (
                    app_username, email, password_hash, display_name,
                    is_active, ai_quota_monthly, ai_quota_used,
                    ai_quota_reset_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, 0, ?, ?, ?)
                """,
                (
                    app_username, email.lower().strip(), password_hash,
                    display_name or app_username, ai_quota_monthly,
                    now + _AI_QUOTA_RESET_DAYS * 86400, now, now,
                ),
            )
            await db.commit()
            return cursor.lastrowid

    # ── 通用更新 ─────────────────────────────────────────────────────────

    async def activate(self, user_id: int) -> None:
        async with self._db() as db:
            await db.execute(
                "UPDATE users SET is_active = 1, updated_at = ? WHERE id = ?",
                (int(time.time()), user_id),
            )
            await db.commit()

    async def set_lang(self, user_id: int, lang: str) -> None:
        now = int(time.time())
        async with self._db() as db:
            await db.execute(
                "UPDATE users SET lang = ?, updated_at = ? WHERE id = ?",
                (lang, now, user_id),
            )
            await db.commit()

    async def touch(self, user_id: int) -> None:
        now = int(time.time())
        async with self._db() as db:
            await db.execute(
                "UPDATE users SET last_seen = ?, updated_at = ? WHERE id = ?",
                (now, now, user_id),
            )
            await db.commit()

    async def update_profile(
        self,
        user_id: int,
        *,
        display_name: Optional[str] = None,
        avatar_url: Optional[str] = None,
    ) -> None:
        fields, params = [], []
        if display_name is not None:
            fields.append("display_name = ?")
            params.append(display_name)
        if avatar_url is not None:
            fields.append("avatar_url = ?")
            params.append(avatar_url)
        if not fields:
            return
        fields.append("updated_at = ?")
        params.append(int(time.time()))
        params.append(user_id)
        async with self._db() as db:
            await db.execute(
                f"UPDATE users SET {', '.join(fields)} WHERE id = ?", params
            )
            await db.commit()

    async def update_password(self, user_id: int, password_hash: str) -> None:
        async with self._db() as db:
            await db.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (password_hash, int(time.time()), user_id),
            )
            await db.commit()

    async def bind_tg(self, user_id: int, tg_user_id: int) -> None:
        async with self._db() as db:
            await db.execute(
                "UPDATE users SET tg_user_id = ?, updated_at = ? WHERE id = ?",
                (tg_user_id, int(time.time()), user_id),
            )
            await db.commit()

    async def unbind_tg(self, user_id: int) -> None:
        async with self._db() as db:
            await db.execute(
                "UPDATE users SET tg_user_id = NULL, updated_at = ? WHERE id = ?",
                (int(time.time()), user_id),
            )
            await db.commit()

    # ── AI 配额 ──────────────────────────────────────────────────────────

    async def check_and_deduct_ai_quota(
        self, user_id: int
    ) -> tuple[bool, int]:
        now = int(time.time())
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT ai_quota_monthly, ai_quota_used, ai_quota_reset_at "
                    "FROM users WHERE id = ?",
                    (user_id,),
                ) as cur:
                    row = await cur.fetchone()
                if not row:
                    await db.execute("ROLLBACK")
                    return False, 0

                monthly, used, reset_at = row[0], row[1], row[2]

                if now >= reset_at:
                    used = 0
                    reset_at = now + _AI_QUOTA_RESET_DAYS * 86400
                    await db.execute(
                        "UPDATE users SET ai_quota_used = 0, ai_quota_reset_at = ? "
                        "WHERE id = ?",
                        (reset_at, user_id),
                    )

                if monthly == -1:  # 无限
                    await db.execute(
                        "UPDATE users SET ai_quota_used = ai_quota_used + 1 "
                        "WHERE id = ?",
                        (user_id,),
                    )
                    await db.commit()
                    return True, -1

                if used >= monthly:
                    await db.execute("ROLLBACK")
                    return False, 0

                await db.execute(
                    "UPDATE users SET ai_quota_used = ai_quota_used + 1 "
                    "WHERE id = ?",
                    (user_id,),
                )
                await db.commit()
                return True, monthly - used - 1
            except Exception:
                await db.execute("ROLLBACK")
                raise

    # ── Tier / Rate limit（保持兼容）────────────────────────────────────

    async def get_tier(self, user_id: int) -> str:
        async with self._db() as db:
            async with db.execute(
                "SELECT tier FROM user_rate_tiers WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else "normal"

    async def get_tier_and_limit(
        self, user_id: int
    ) -> Optional[tuple[str, Optional[int]]]:
        async with self._db() as db:
            async with db.execute(
                "SELECT tier, max_per_hour FROM user_rate_tiers WHERE user_id = ?",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
                return (row[0], row[1]) if row else None

    async def set_tier(
        self,
        user_id: int,
        tier: str,
        note: str = "",
        set_by: Optional[int] = None,
        custom_max: Optional[int] = None,
    ) -> None:
        async with self._db() as db:
            await db.execute(
                """
                INSERT INTO user_rate_tiers
                    (user_id, tier, max_per_hour, note, set_by, set_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    tier = excluded.tier, max_per_hour = excluded.max_per_hour,
                    note = excluded.note, set_by = excluded.set_by,
                    set_at = excluded.set_at
                """,
                (user_id, tier, custom_max, note, set_by, int(time.time())),
            )
            await db.commit()

    # ── Refresh tokens & 验证码（原 AppUserRepository 移过来）───────────

    async def create_refresh_token(
        self, user_id: int, device_hint: str = ""
    ) -> str:
        raw = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        now = int(time.time())
        async with self._db() as db:
            await db.execute(
                """
                INSERT INTO refresh_tokens
                    (app_user_id, token_hash, expires_at, is_revoked, device_hint, created_at)
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (user_id, token_hash, now + _REFRESH_TOKEN_TTL,
                 device_hint[:48], now),
            )
            await db.commit()
        return raw

    async def verify_and_rotate_refresh_token(
        self, raw_token: str, device_hint: str = ""
    ) -> Optional[tuple[int, str]]:
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        now = int(time.time())
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    """
                    SELECT id, app_user_id FROM refresh_tokens
                    WHERE token_hash = ? AND is_revoked = 0 AND expires_at > ?
                    """,
                    (token_hash, now),
                ) as cur:
                    row = await cur.fetchone()
                if not row:
                    await db.execute("ROLLBACK")
                    return None
                rt_id, user_id = row[0], row[1]
                await db.execute(
                    "UPDATE refresh_tokens SET is_revoked = 1 WHERE id = ?",
                    (rt_id,),
                )
                new_raw = secrets.token_urlsafe(48)
                new_hash = hashlib.sha256(new_raw.encode()).hexdigest()
                await db.execute(
                    """
                    INSERT INTO refresh_tokens
                        (app_user_id, token_hash, expires_at, is_revoked, device_hint, created_at)
                    VALUES (?, ?, ?, 0, ?, ?)
                    """,
                    (user_id, new_hash, now + _REFRESH_TOKEN_TTL,
                     device_hint[:48], now),
                )
                await db.commit()
                return user_id, new_raw
            except Exception:
                await db.execute("ROLLBACK")
                raise

    async def revoke_refresh_token(self, raw_token: str) -> bool:
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        async with self._db() as db:
            cursor = await db.execute(
                "UPDATE refresh_tokens SET is_revoked = 1 WHERE token_hash = ?",
                (token_hash,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def revoke_all_refresh_tokens(self, user_id: int) -> None:
        async with self._db() as db:
            await db.execute(
                "UPDATE refresh_tokens SET is_revoked = 1 WHERE app_user_id = ?",
                (user_id,),
            )
            await db.commit()

    async def create_verify_code(
        self, user_id: int, purpose: str = "activation"
    ) -> str:
        code = str(secrets.randbelow(900000) + 100000)
        now = int(time.time())
        async with self._db() as db:
            await db.execute(
                "UPDATE email_verifications SET is_used = 1 "
                "WHERE app_user_id = ? AND purpose = ? AND is_used = 0",
                (user_id, purpose),
            )
            await db.execute(
                """
                INSERT INTO email_verifications
                    (app_user_id, code, purpose, expires_at, is_used, created_at)
                VALUES (?, ?, ?, ?, 0, ?)
                """,
                (user_id, code, purpose, now + _VERIFY_CODE_TTL, now),
            )
            await db.commit()
        return code

    async def consume_verify_code(
        self, user_id: int, code: str, purpose: str = "activation"
    ) -> bool:
        now = int(time.time())
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    """
                    SELECT id FROM email_verifications
                    WHERE app_user_id = ? AND code = ? AND purpose = ?
                      AND is_used = 0 AND expires_at > ?
                    """,
                    (user_id, code, purpose, now),
                ) as cur:
                    row = await cur.fetchone()
                if not row:
                    await db.execute("ROLLBACK")
                    return False
                await db.execute(
                    "UPDATE email_verifications SET is_used = 1 WHERE id = ?",
                    (row[0],),
                )
                await db.commit()
                return True
            except Exception:
                await db.execute("ROLLBACK")
                raise

    async def create_bind_code(self, user_id: int) -> str:
        code = str(secrets.randbelow(900000) + 100000)
        now = int(time.time())
        async with self._db() as db:
            await db.execute(
                "UPDATE tg_bind_codes SET is_used = 1 "
                "WHERE app_user_id = ? AND is_used = 0",
                (user_id,),
            )
            await db.execute(
                "INSERT INTO tg_bind_codes "
                "(app_user_id, code, expires_at, is_used, created_at) "
                "VALUES (?, ?, ?, 0, ?)",
                (user_id, code, now + _BIND_CODE_TTL, now),
            )
            await db.commit()
        return code

    async def consume_bind_code(
        self, code: str, tg_user_id: int
    ) -> Optional[int]:
        now = int(time.time())
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    """
                    SELECT id, app_user_id FROM tg_bind_codes
                    WHERE code = ? AND is_used = 0 AND expires_at > ?
                    """,
                    (code, now),
                ) as cur:
                    row = await cur.fetchone()
                if not row:
                    await db.execute("ROLLBACK")
                    return None
                bind_id, user_id = row[0], row[1]
                # 检查 TG 是否已被其他人绑定
                async with db.execute(
                    "SELECT id FROM users WHERE tg_user_id = ? AND id != ?",
                    (tg_user_id, user_id),
                ) as cur:
                    if await cur.fetchone():
                        await db.execute("ROLLBACK")
                        return None
                await db.execute(
                    "UPDATE tg_bind_codes SET is_used = 1 WHERE id = ?",
                    (bind_id,),
                )
                await db.execute(
                    "UPDATE users SET tg_user_id = ?, updated_at = ? WHERE id = ?",
                    (tg_user_id, now, user_id),
                )
                await db.commit()
                return user_id
            except Exception:
                await db.execute("ROLLBACK")
                raise

    async def set_role(self, user_id: int, role: str) -> None:
        """设置用户角色（'user' | 'admin'）。"""
        async with self._db() as db:
            await db.execute(
                "UPDATE users SET role = ?, updated_at = ? WHERE id = ?",
                (role, int(time.time()), user_id),
            )
            await db.commit()

    async def get_role(self, user_id: int) -> str:
        """返回用户角色，不存在时返回 'user'。"""
        async with self._db() as db:
            async with db.execute(
                "SELECT role FROM users WHERE id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return (row[0] or "user") if row else "user"

    async def set_permissions(
        self, user_id: int, permissions: list[str]
    ) -> None:
        """
        覆盖写入用户的功能权限列表（JSON 数组序列化存储）。

        permissions 示例：["bot_text", "bot_receipt", "app_ocr"]
        """
        import json as _json
        value = _json.dumps(permissions, ensure_ascii=False)
        async with self._db() as db:
            await db.execute(
                "UPDATE users SET permissions = ?, updated_at = ? WHERE id = ?",
                (value, int(time.time()), user_id),
            )
            await db.commit()

    async def get_permissions(self, user_id: int) -> list[str]:
        """返回用户权限列表，解析失败或不存在时返回空列表。"""
        import json as _json
        async with self._db() as db:
            async with db.execute(
                "SELECT permissions FROM users WHERE id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
        if not row or not row[0]:
            return []
        try:
            result = _json.loads(row[0])
            return result if isinstance(result, list) else []
        except (ValueError, TypeError):
            return []

    async def has_permission(self, user_id: int, perm: str) -> bool:
        """快速检查用户是否拥有某个权限标识。"""
        perms = await self.get_permissions(user_id)
        return perm in perms

    # ── IP 审计 ──────────────────────────────────────────────────────────

    async def set_registration_ip(self, user_id: int, ip: str) -> None:
        """记录注册 IP（首次注册时调用一次，后续不覆盖）。"""
        async with self._db() as db:
            await db.execute(
                """
                UPDATE users SET registration_ip = ?, updated_at = ?
                WHERE id = ? AND (registration_ip IS NULL OR registration_ip = '')
                """,
                (ip, int(time.time()), user_id),
            )
            await db.commit()

    async def update_last_login_ip(self, user_id: int, ip: str) -> None:
        """每次登录/鉴权通过后更新最后登录 IP。"""
        async with self._db() as db:
            await db.execute(
                "UPDATE users SET last_login_ip = ?, updated_at = ? WHERE id = ?",
                (ip, int(time.time()), user_id),
            )
            await db.commit()

    # ── 管理员专用：用户列表 ─────────────────────────────────────────────

    async def list_all_for_admin(
        self,
        page: int = 1,
        page_size: int = 50,
        keyword: Optional[str] = None,
        role: Optional[str] = None,
        is_active: Optional[int] = None,
    ) -> tuple[list[dict], int]:
        """
        管理员用户列表，支持关键词搜索、角色过滤、激活状态过滤。
        返回 (rows, total_count)。
        """
        conds = ["1=1"]
        params: list = []

        if keyword:
            kw = f"%{keyword}%"
            conds.append(
                "(email LIKE ? OR app_username LIKE ? "
                "OR tg_username LIKE ? OR display_name LIKE ?)"
            )
            params.extend([kw, kw, kw, kw])

        if role is not None:
            conds.append("role = ?")
            params.append(role)

        if is_active is not None:
            conds.append("is_active = ?")
            params.append(is_active)

        where = " AND ".join(conds)
        offset = (page - 1) * page_size

        async with self._db() as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM users WHERE {where}", params
            ) as cur:
                total = (await cur.fetchone())[0]

            async with db.execute(
                f"""
                SELECT id, app_username, email, display_name, tg_user_id,
                       tg_username, is_active, role, permissions,
                       ai_quota_monthly, ai_quota_used, ai_quota_reset_at,
                       registration_ip, last_login_ip,
                       created_at, updated_at, last_seen
                FROM users
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                params + [page_size, offset],
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]

        return rows, total

    async def set_active(self, user_id: int, is_active: int) -> bool:
        """封禁 (is_active=0) 或解封 (is_active=1) 用户。"""
        async with self._db() as db:
            cursor = await db.execute(
                "UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?",
                (is_active, int(time.time()), user_id),
            )
            await db.commit()
            return cursor.rowcount > 0
