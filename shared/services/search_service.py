# shared/services/search_service.py
"""
Meilisearch 搜索服务。

职责
----
- 初始化索引配置（字段、过滤器、同义词）
- 写入/删除单条账单
- 搜索账单（支持错别字容错、假名、同义词）

设计原则
--------
- SQLite 是主数据库，Meilisearch 只做搜索索引
- 索引失败只记录日志，不阻断主流程（写入 SQLite 优先）
- 无关键词浏览走 SQLite，有关键词才走 Meilisearch
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 索引名称
_INDEX = "bills"


def _get_client():
    """
    按需创建 AsyncClient。
    每次调用新建连接，meilisearch-python-sdk 的 AsyncClient
    设计为 async context manager，不需要全局复用。
    """
    from meilisearch_python_sdk import AsyncClient
    from config.settings import MEILISEARCH_URL, MEILISEARCH_API_KEY

    return AsyncClient(MEILISEARCH_URL, MEILISEARCH_API_KEY or None)


# ---------------------------------------------------------------------------
# 启动初始化
# ---------------------------------------------------------------------------

async def init_index() -> None:
    """
    启动时幂等地初始化索引配置。
    Meilisearch 服务不可用时只打警告，不阻断 Bot 启动。
    """
    try:
        async with _get_client() as client:
            # 确保索引存在
            await client.create_index(_INDEX, primary_key="id")
    except Exception:
        pass  # 索引已存在时会抛异常，忽略

    try:
        async with _get_client() as client:
            index = client.index(_INDEX)

            # 可搜索字段（顺序即权重，靠前的匹配得分更高）
            await index.update_searchable_attributes([
                "merchant",
                "description",
                "category",
            ])

            # 用于 filter 的字段
            await index.update_filterable_attributes([
                "user_id",
                "bill_date",
                "currency",
                "category",
            ])

            # 排序字段
            await index.update_sortable_attributes([
                "bill_date",
                "amount",
                "created_at",
            ])

            # 同义词（双向自动处理）
            await index.update_synonyms({
                "マクドナルド": ["マック", "麦当劳", "McDonald's", "mcdonalds"],
                "スターバックス": ["スタバ", "星巴克", "Starbucks", "starbucks"],
                "サイゼリヤ":    ["萨莉亚", "Saizeriya", "saizeriya"],
                "セブンイレブン": ["7-Eleven", "711", "セブン"],
                "ファミリーマート": ["ファミマ", "FamilyMart"],
                "ローソン":      ["Lawson", "lawson"],
                "餐饮":         ["食事", "飲食", "グルメ", "食品"],
                "交通":         ["電車", "バス", "タクシー", "交通費"],
                "购物":         ["ショッピング", "買い物"],
            })

            # 错别字容错级别：0=关闭 1=宽松 2=严格（默认）
            # 保持默认，Meilisearch 会自动处理 1~2 个字符的错误
            await index.update_typo_tolerance({
                "enabled": True,
                "minWordSizeForTypos": {
                    "oneTypo": 4,    # 4个字符以上才允许1个错别字
                    "twoTypos": 8,   # 8个字符以上才允许2个错别字
                },
            })

        logger.info("Meilisearch index '%s' initialized", _INDEX)

    except Exception as e:
        logger.warning(
            "Meilisearch init_index failed (search degraded to LIKE): %s", e
        )


# ---------------------------------------------------------------------------
# 写入 / 删除
# ---------------------------------------------------------------------------

async def index_bill(bill: dict) -> None:
    """
    新增或更新账单索引。
    失败时只记录日志，不抛异常（不阻断主流程）。
    """
    try:
        async with _get_client() as client:
            await client.index(_INDEX).add_documents([bill], primary_key="id")
        logger.debug("Meilisearch indexed bill id=%s", bill.get("id"))
    except Exception as e:
        logger.warning("Meilisearch index_bill failed id=%s: %s", bill.get("id"), e)


async def index_bills_bulk(bills: list[dict]) -> None:
    """
    批量写入账单索引（迁移脚本使用）。
    """
    if not bills:
        return
    try:
        async with _get_client() as client:
            task = await client.index(_INDEX).add_documents(bills, primary_key="id")
        logger.info("Meilisearch bulk indexed %d bills, task_uid=%s",
                    len(bills), getattr(task, "task_uid", "?"))
    except Exception as e:
        logger.error("Meilisearch bulk index failed: %s", e)
        raise


async def delete_bill_from_index(bill_id: int) -> None:
    """
    从索引删除账单。
    失败时只记录日志。
    """
    try:
        async with _get_client() as client:
            await client.index(_INDEX).delete_document(bill_id)
        logger.debug("Meilisearch deleted bill id=%s", bill_id)
    except Exception as e:
        logger.warning("Meilisearch delete_bill failed id=%s: %s", bill_id, e)


async def update_bill_in_index(bill_id: int, fields: dict) -> None:
    """
    更新索引中账单的部分字段（PATCH 操作时使用）。
    """
    try:
        async with _get_client() as client:
            await client.index(_INDEX).update_documents(
                [{"id": bill_id, **fields}]
            )
    except Exception as e:
        logger.warning("Meilisearch update_bill failed id=%s: %s", bill_id, e)


# ---------------------------------------------------------------------------
# 搜索
# ---------------------------------------------------------------------------

async def search_bills(
    user_id: int,
    keyword: str,
    year: Optional[int] = None,
    month: Optional[int] = None,
    page: int = 1,
    page_size: int = 20,
) -> Optional[dict]:
    """
    通过 Meilisearch 搜索账单。

    Returns
    -------
    dict  搜索结果，格式与 SQLite 路径一致
    None  Meilisearch 不可用时返回 None，调用方降级到 LIKE 搜索
    """
    filters: list[str] = [f"user_id = {user_id}"]

    if year and month:
        prefix = f"{year:04d}-{month:02d}"
        # bill_date 格式为 YYYY-MM-DD，用前缀过滤
        filters.append(f"bill_date >= '{prefix}-01'")
        # 月末最多 31 天，用 32 确保覆盖所有情况
        filters.append(f"bill_date <= '{prefix}-31'")
    elif year:
        filters.append(f"bill_date >= '{year:04d}-01-01'")
        filters.append(f"bill_date <= '{year:04d}-12-31'")

    try:
        async with _get_client() as client:
            result = await client.index(_INDEX).search(
                keyword,
                filter=" AND ".join(filters),
                limit=page_size,
                offset=(page - 1) * page_size,
                sort=["bill_date:desc"],
            )

        total = result.estimated_total_hits or 0
        return {
            "bills": result.hits,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_next": (page * page_size) < total,
        }

    except Exception as e:
        logger.warning(
            "Meilisearch search failed (falling back to LIKE): %s", e
        )
        return None  # 降级信号
