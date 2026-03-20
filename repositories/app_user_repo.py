# repositories/app_user_repo.py
"""
AppUserRepository — app_users / refresh_tokens / email_verifications 的全部 SQL。
"""
from __future__ import annotations

import hashlib
import secrets
import time
import logging
from typing import Optional

from shared.repositories.base import BaseRepository

logger = logging.getLogger(__name__)

_REFRESH_TOKEN_TTL  = 30 * 24 * 3600   # 30 天
_VERIFY_CODE_TTL    = 10 * 60          # 10 分钟
_AI_QUOTA_RESET_DAYS = 30              # 每30天重置配额
_BIND_CODE_TTL = 10 * 60   # 10 分钟

class AppUserRepository(BaseRepository):

    # ────────────────────────────── app_users ──────────────────────────────

    async def create(
        self,
        *,
        username: str,
        email: str,
        password_hash: str,
        display_name: str = "",
        ai_quota_monthly: int = 100,
    ) -> int:
        now = time.time()
        async with self._db() as db:
            cursor = await db.execute(
                """
                INSERT INTO app_users
                    (username, email, password_hash, display_name,
                     is_active, ai_quota_monthly, ai_quota_used,
                     ai_quota_reset_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, ?, 0, ?, ?, ?)
                """,
                (username, email, password_hash, display_name or username,
                 ai_quota_monthly, now + _AI_QUOTA_RESET_DAYS * 86400, now, now),
            )
            await db.commit()
            return cursor.lastrowid

    async def get_by_id(self, user_id: int) -> Optional[dict]:
        async with self._db() as db:
            async with db.execute(
                "SELECT * FROM app_users WHERE id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_by_email(self, email: str) -> Optional[dict]:
        async with self._db() as db:
            async with db.execute(
                "SELECT * FROM app_users WHERE email = ?", (email.lower().strip(),)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_by_username(self, username: str) -> Optional[dict]:
        async with self._db() as db:
            async with db.execute(
                "SELECT * FROM app_users WHERE username = ?", (username.strip(),)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_by_identifier(self, identifier: str) -> Optional[dict]:
        """邮箱或用户名均可登录。"""
        if "@" in identifier:
            return await self.get_by_email(identifier)
        return await self.get_by_username(identifier)

    async def get_by_tg_user_id(self, tg_user_id: int) -> Optional[dict]:
        async with self._db() as db:
            async with db.execute(
                "SELECT * FROM app_users WHERE tg_user_id = ?", (tg_user_id,)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def activate(self, user_id: int) -> None:
        async with self._db() as db:
            await db.execute(
                "UPDATE app_users SET is_active = 1, updated_at = ? WHERE id = ?",
                (time.time(), user_id),
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
            fields.append("display_name = ?"); params.append(display_name)
        if avatar_url is not None:
            fields.append("avatar_url = ?"); params.append(avatar_url)
        if not fields:
            return
        fields.append("updated_at = ?"); params.append(time.time())
        params.append(user_id)
        async with self._db() as db:
            await db.execute(
                f"UPDATE app_users SET {', '.join(fields)} WHERE id = ?", params
            )
            await db.commit()

    async def update_password(self, user_id: int, password_hash: str) -> None:
        async with self._db() as db:
            await db.execute(
                "UPDATE app_users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (password_hash, time.time(), user_id),
            )
            await db.commit()

    async def bind_tg(self, user_id: int, tg_user_id: int) -> None:
        async with self._db() as db:
            await db.execute(
                "UPDATE app_users SET tg_user_id = ?, updated_at = ? WHERE id = ?",
                (tg_user_id, time.time(), user_id),
            )
            await db.commit()

    async def unbind_tg(self, user_id: int) -> None:
        async with self._db() as db:
            await db.execute(
                "UPDATE app_users SET tg_user_id = NULL, updated_at = ? WHERE id = ?",
                (time.time(), user_id),
            )
            await db.commit()

    # ────────────────── AI 配额（原子扣减，防并发超用） ───────────────────

    async def check_and_deduct_ai_quota(self, user_id: int) -> tuple[bool, int]:
        """
        原子扣减 AI 配额。
        Returns (allowed, remaining)
        -1 quota = 无限制。
        同时处理按月自动重置。
        """
        now = time.time()
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT ai_quota_monthly, ai_quota_used, ai_quota_reset_at "
                    "FROM app_users WHERE id = ?", (user_id,)
                ) as cur:
                    row = await cur.fetchone()

                if not row:
                    await db.execute("ROLLBACK")
                    return False, 0

                monthly, used, reset_at = row[0], row[1], row[2]

                # 自动重置
                if now >= reset_at:
                    used = 0
                    reset_at = now + _AI_QUOTA_RESET_DAYS * 86400
                    await db.execute(
                        "UPDATE app_users SET ai_quota_used = 0, ai_quota_reset_at = ? WHERE id = ?",
                        (reset_at, user_id),
                    )

                # -1 = 无限
                if monthly == -1:
                    await db.execute(
                        "UPDATE app_users SET ai_quota_used = ai_quota_used + 1 WHERE id = ?",
                        (user_id,),
                    )
                    await db.commit()
                    return True, -1

                if used >= monthly:
                    await db.execute("ROLLBACK")
                    return False, 0

                await db.execute(
                    "UPDATE app_users SET ai_quota_used = ai_quota_used + 1 WHERE id = ?",
                    (user_id,),
                )
                await db.commit()
                return True, monthly - used - 1

            except Exception:
                await db.execute("ROLLBACK")
                raise

    # ────────────────────────── refresh_tokens ────────────────────────────

    async def create_refresh_token(
        self, user_id: int, device_hint: str = ""
    ) -> str:
        """生成 refresh token，存 hash，返回明文（只出现一次）。"""
        raw = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        now = time.time()
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
        """
        校验 refresh token，有效则吊销旧的、签发新的（旋转策略）。
        Returns (app_user_id, new_raw_token) or None
        """
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        now = time.time()

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

                # 吊销旧 token
                await db.execute(
                    "UPDATE refresh_tokens SET is_revoked = 1 WHERE id = ?", (rt_id,)
                )

                # 签发新 token
                new_raw = secrets.token_urlsafe(48)
                new_hash = hashlib.sha256(new_raw.encode()).hexdigest()
                await db.execute(
                    """
                    INSERT INTO refresh_tokens
                        (app_user_id, token_hash, expires_at, is_revoked, device_hint, created_at)
                    VALUES (?, ?, ?, 0, ?, ?)
                    """,
                    (user_id, new_hash, now + _REFRESH_TOKEN_TTL, device_hint[:48], now),
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
        """退出全部设备时使用。"""
        async with self._db() as db:
            await db.execute(
                "UPDATE refresh_tokens SET is_revoked = 1 WHERE app_user_id = ?",
                (user_id,),
            )
            await db.commit()

    # ─────────────────────── email_verifications ──────────────────────────

    async def create_verify_code(
        self, user_id: int, purpose: str = "activation"
    ) -> str:
        """生成6位数字验证码，同一用途只保留最新一条（旧的自动失效）。"""
        code = str(secrets.randbelow(900000) + 100000)  # 100000-999999
        now = time.time()
        async with self._db() as db:
            # 旧验证码失效
            await db.execute(
                """
                UPDATE email_verifications SET is_used = 1
                WHERE app_user_id = ? AND purpose = ? AND is_used = 0
                """,
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
        """校验并核销验证码。"""
        now = time.time()
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

    async def create_bind_code(self, app_user_id: int) -> str:
        """生成 6 位 TG 绑定码，同一账号旧码自动失效。"""
        code = str(secrets.randbelow(900000) + 100000)
        now  = time.time()
        async with self._db() as db:
            # 旧码失效
            await db.execute(
                "UPDATE tg_bind_codes SET is_used = 1 WHERE app_user_id = ? AND is_used = 0",
                (app_user_id,),
            )
            await db.execute(
                """
                INSERT INTO tg_bind_codes (app_user_id, code, expires_at, is_used, created_at)
                VALUES (?, ?, ?, 0, ?)
                """,
                (app_user_id, code, now + _BIND_CODE_TTL, now),
            )
            await db.commit()
        return code

    async def consume_bind_code(self, code: str, tg_user_id: int) -> Optional[int]:
        """
        核销绑定码并写入 tg_user_id。
        成功返回 app_user_id，失败返回 None。
        """
        now = time.time()
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

                bind_id, app_user_id = row[0], row[1]

                # 检查 tg_user_id 是否已被其他账号绑定
                async with db.execute(
                    "SELECT id FROM app_users WHERE tg_user_id = ? AND id != ?",
                    (tg_user_id, app_user_id),
                ) as cur:
                    conflict = await cur.fetchone()

                if conflict:
                    await db.execute("ROLLBACK")
                    return None

                # 核销绑定码
                await db.execute(
                    "UPDATE tg_bind_codes SET is_used = 1 WHERE id = ?",
                    (bind_id,),
                )
                # 写入 tg_user_id
                await db.execute(
                    "UPDATE app_users SET tg_user_id = ?, updated_at = ? WHERE id = ?",
                    (tg_user_id, now, app_user_id),
                )
                await db.commit()
                return app_user_id

            except Exception:
                await db.execute("ROLLBACK")
                raise

    async def get_bind_code_owner(self, code: str) -> Optional[dict]:
        """查询绑定码对应的 app_user 信息（Bot 侧展示确认信息用）。"""
        now = time.time()
        async with self._db() as db:
            async with db.execute(
                """
                SELECT au.id, au.username, au.display_name, au.email
                FROM tg_bind_codes bc
                JOIN app_users au ON bc.app_user_id = au.id
                WHERE bc.code = ? AND bc.is_used = 0 AND bc.expires_at > ?
                """,
                (code, now),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None
