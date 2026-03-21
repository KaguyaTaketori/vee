# api/routes/groups.py
"""
GET    /v1/groups/me          获取我的账本
POST   /v1/groups             创建账本
POST   /v1/groups/join        加入账本（invite_code）
PATCH  /v1/groups/{id}        修改账本信息

GET    /v1/accounts           账户列表
POST   /v1/accounts           创建账户
PATCH  /v1/accounts/{id}      修改账户
DELETE /v1/accounts/{id}      停用账户
"""
from __future__ import annotations

import logging
import secrets
import time
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import require_active_user
from api.schemas_v2 import (
    GroupOut, GroupCreate,
    AccountOut, AccountCreate, AccountPatch,
)
from database.db import get_db
from utils.currency import int_to_amount

logger = logging.getLogger(__name__)
router = APIRouter(tags=["groups & accounts"])


# ─────────────────────────────────────────────────────────────────────────────
# 内部工具
# ─────────────────────────────────────────────────────────────────────────────

def _row_to_group(row: dict) -> GroupOut:
    return GroupOut(
        id=row["id"],
        name=row["name"],
        owner_id=row["owner_id"],
        invite_code=row["invite_code"],
        base_currency=row["base_currency"],
        is_active=bool(row["is_active"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _row_to_account(row: dict) -> AccountOut:
    return AccountOut(
        id=row["id"],
        name=row["name"],
        type=row["type"],
        currency_code=row["currency_code"],
        group_id=row["group_id"],
        balance_cache=row.get("balance_cache", 0),
        balance_updated_at=float(row["balance_updated_at"])
            if row.get("balance_updated_at") else None,
        is_active=bool(row["is_active"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


async def _require_group_member(db, group_id: int, user_id: int) -> dict:
    """确认用户属于该 group，否则 403。"""
    async with db.execute(
        "SELECT id FROM groups WHERE id = ? AND is_active = 1", (group_id,)
    ) as cur:
        group = await cur.fetchone()
    if not group:
        raise HTTPException(status_code=404, detail="账本不存在")

    async with db.execute(
        "SELECT id FROM users WHERE id = ? AND group_id = ?",
        (user_id, group_id),
    ) as cur:
        member = await cur.fetchone()
    if not member:
        raise HTTPException(status_code=403, detail="您不属于该账本")
    return dict(group)


# ─────────────────────────────────────────────────────────────────────────────
# Group 路由
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/groups/me", response_model=GroupOut)
async def get_my_group(
    user_id: Annotated[int, Depends(require_active_user)],
):
    """获取当前用户所在的账本。"""
    async with get_db() as db:
        async with db.execute(
            """
            SELECT g.* FROM groups g
            JOIN users u ON u.group_id = g.id
            WHERE u.id = ? AND g.is_active = 1
            """,
            (user_id,),
        ) as cur:
            row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="尚未加入任何账本")
    return _row_to_group(dict(row))


@router.post("/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(
    body: GroupCreate,
    user_id: Annotated[int, Depends(require_active_user)],
):
    """创建新账本，自动成为群主，并初始化一个默认现金账户。"""
    now         = int(time.time())
    invite_code = secrets.token_urlsafe(8)

    async with get_db() as db:
        # 1. 建 group
        cursor = await db.execute(
            """
            INSERT INTO groups
                (name, owner_id, invite_code, base_currency, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (body.name, user_id, invite_code, body.base_currency, now, now),
        )
        group_id = cursor.lastrowid

        # 2. 关联用户
        await db.execute(
            "UPDATE users SET group_id = ?, updated_at = ? WHERE id = ?",
            (group_id, now, user_id),
        )

        # 3. 初始化默认现金账户
        await db.execute(
            """
            INSERT INTO accounts
                (name, type, currency_code, group_id,
                 balance_cache, is_active, created_at, updated_at)
            VALUES ('现金', 'cash', ?, ?, 0, 1, ?, ?)
            """,
            (body.base_currency, group_id, now, now),
        )

        await db.commit()

        async with db.execute(
            "SELECT * FROM groups WHERE id = ?", (group_id,)
        ) as cur:
            row = dict(await cur.fetchone())

    logger.info("Group created: id=%d owner=%d", group_id, user_id)
    return _row_to_group(row)


@router.post("/groups/join", response_model=GroupOut)
async def join_group(
    invite_code: str,
    user_id: Annotated[int, Depends(require_active_user)],
):
    """通过邀请码加入账本。"""
    now = int(time.time())
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM groups WHERE invite_code = ? AND is_active = 1",
            (invite_code,),
        ) as cur:
            row = await cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="邀请码无效或已过期")

        group = dict(row)

        # 检查是否已在其他 group
        async with db.execute(
            "SELECT group_id FROM users WHERE id = ?", (user_id,)
        ) as cur:
            user_row = await cur.fetchone()
        if user_row and user_row[0] and user_row[0] != group["id"]:
            raise HTTPException(
                status_code=400, detail="您已在其他账本中，请先退出"
            )

        await db.execute(
            "UPDATE users SET group_id = ?, updated_at = ? WHERE id = ?",
            (group["id"], now, user_id),
        )
        await db.commit()

    logger.info("User %d joined group %d", user_id, group["id"])
    return _row_to_group(group)


@router.patch("/groups/{group_id}", response_model=GroupOut)
async def patch_group(
    group_id: int,
    name: Optional[str] = None,
    base_currency: Optional[str] = None,
    user_id: Annotated[int, Depends(require_active_user)] = None,
):
    now = int(time.time())
    async with get_db() as db:
        # 只有群主才能修改
        async with db.execute(
            "SELECT * FROM groups WHERE id = ? AND owner_id = ?",
            (group_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=403, detail="只有群主可以修改账本信息")

        fields, params = [], []
        if name:
            fields.append("name = ?"); params.append(name)
        if base_currency:
            fields.append("base_currency = ?"); params.append(base_currency)
        if not fields:
            return _row_to_group(dict(row))

        fields.append("updated_at = ?"); params.append(now)
        params.append(group_id)
        await db.execute(
            f"UPDATE groups SET {', '.join(fields)} WHERE id = ?", params
        )
        await db.commit()

        async with db.execute("SELECT * FROM groups WHERE id = ?", (group_id,)) as cur:
            updated = dict(await cur.fetchone())

    return _row_to_group(updated)


# ─────────────────────────────────────────────────────────────────────────────
# Account 路由
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(
    group_id: int,
    user_id: Annotated[int, Depends(require_active_user)],
):
    async with get_db() as db:
        await _require_group_member(db, group_id, user_id)
        async with db.execute(
            """
            SELECT * FROM accounts
            WHERE group_id = ? AND is_active = 1
            ORDER BY id ASC
            """,
            (group_id,),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    return [_row_to_account(r) for r in rows]


@router.post("/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(
    body: AccountCreate,
    user_id: Annotated[int, Depends(require_active_user)],
):
    now = int(time.time())
    async with get_db() as db:
        await _require_group_member(db, body.group_id, user_id)
        cursor = await db.execute(
            """
            INSERT INTO accounts
                (name, type, currency_code, group_id,
                 balance_cache, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, 1, ?, ?)
            """,
            (body.name, body.type.value, body.currency_code,
             body.group_id, now, now),
        )
        account_id = cursor.lastrowid
        await db.commit()

        async with db.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ) as cur:
            row = dict(await cur.fetchone())

    return _row_to_account(row)


@router.patch("/accounts/{account_id}", response_model=AccountOut)
async def patch_account(
    account_id: int,
    body: AccountPatch,
    user_id: Annotated[int, Depends(require_active_user)],
):
    now = int(time.time())
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="账户不存在")
        account = dict(row)
        await _require_group_member(db, account["group_id"], user_id)

        fields, params = [], []
        updates = body.model_dump(exclude_none=True)
        for k, v in updates.items():
            fields.append(f"{k} = ?"); params.append(v)
        if not fields:
            return _row_to_account(account)

        fields.append("updated_at = ?"); params.append(now)
        params.append(account_id)
        await db.execute(
            f"UPDATE accounts SET {', '.join(fields)} WHERE id = ?", params
        )
        await db.commit()

        async with db.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ) as cur:
            updated = dict(await cur.fetchone())

    return _row_to_account(updated)


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_account(
    account_id: int,
    user_id: Annotated[int, Depends(require_active_user)],
):
    """停用账户（软删除）。有未结算流水时不允许停用。"""
    now = int(time.time())
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="账户不存在")
        account = dict(row)
        await _require_group_member(db, account["group_id"], user_id)

        # 检查是否有未同步流水
        async with db.execute(
            """
            SELECT COUNT(*) FROM transactions
            WHERE account_id = ? AND is_deleted = 0
            """,
            (account_id,),
        ) as cur:
            txn_count = (await cur.fetchone())[0]

        if txn_count > 0:
            raise HTTPException(
                status_code=400,
                detail=f"该账户有 {txn_count} 笔流水记录，无法停用。"
                       "请先将流水迁移到其他账户。",
            )

        await db.execute(
            "UPDATE accounts SET is_active = 0, updated_at = ? WHERE id = ?",
            (now, account_id),
        )
        await db.commit()
