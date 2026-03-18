"""
modules/billing/services/bill_parser.py

变更说明（items 支持版本）：
1. _SYSTEM_PROMPT
   - 新增 items 数组字段，要求 LLM 按收据顺序提取每一行商品/折扣/税
   - merchant 识别增强：优先顺序 ① 收据顶部大字店名 ② 信用卡票据段加盟店名
     ③ 登録番号上方株式会社名称；任何情况下禁止填 unknown
   - 明确 item_type 枚举：item / discount / tax / subtotal
2. _build_entry
   - 解析 items 列表，构造 BillItem 对象列表
   - subtotal 行不入 items（仅供 LLM 参考计算合计）
3. 其余逻辑（重试、category 校验、bill_date 校验）不变
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Optional

from llm.manager import LLMManager
from modules.billing.services.bill_cache import BillEntry, BillItem

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 枚举常量
# ---------------------------------------------------------------------------

VALID_CATEGORIES: frozenset[str] = frozenset(
    {"餐饮", "交通", "购物", "娱乐", "医疗", "住房", "水电煤", "其他"}
)

VALID_ITEM_TYPES: frozenset[str] = frozenset({"item", "discount", "tax", "subtotal"})

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """你是一个专业的财务助手，专门从收据/账单中提取消费信息。

【重要规则】
- 只处理实际的消费凭证：收据、发票、账单截图、转账记录等。
- 如果内容是价目表、费率表、菜单、说明书、广告等非消费记录，请返回：
  {"error": "这不是消费凭证，请发送实际的收据或账单"}
- 如果无法确定实际消费金额，请返回：
  {"error": "无法识别有效的消费金额"}

【字段说明】
- amount：实际支付总金额（float），必须大于 0
- currency：货币代码，如 CNY / USD / JPY / HKD / EUR
- category：从以下选项中选择一个：餐饮 / 交通 / 购物 / 娱乐 / 医疗 / 住房 / 水电煤 / 其他
- description：本次消费的简短描述，15字以内，必填，不能为空
- merchant：按以下优先级提取商家名称：
    ① 收据顶部最大字号的店名（如 ベルク、サイゼリヤ 等）
    ② 收据底部「クレジット売上票」区域中的「加盟店名」字段
    ③ 登録番号上方的「株式会社〇〇」名称
    ④ 收据任意位置出现的品牌/店名文字
    - 原样提取，禁止翻译或转换为其他语言
    - 任何情况下禁止填 unknown；实在无法识别则填收据上最显眼的一段可读文字
- bill_date：消费日期，YYYY-MM-DD 格式；没有明确日期则填今天，禁止填 unknown
- items：收据中的商品明细列表（有明细则必填，无明细则填空数组 []）
    每项字段：
    - name：商品名称，翻译为中文，括号内保留原文，如"切块白菜(ざく切り白)"
    - name_raw：收据上的原始文字
    - quantity：数量（默认 1）
    - unit_price：单价，无则 null
    - amount：该行金额；折扣/优惠为负数
    - item_type：
        "item"     = 普通商品
        "discount" = 折扣/优惠（amount 必须为负数）
        "tax"      = 税费
        "subtotal" = 小计行（仅用于核对，不计入明细统计）
    按收据从上到下的顺序排列，每个商品紧跟其对应的折扣行。

【返回格式】
严格返回 JSON，不包含任何额外说明或 Markdown 代码块：
{
  "amount": <float>,
  "currency": "<货币代码>",
  "category": "<类别>",
  "description": "<描述>",
  "merchant": "<商家名>",
  "bill_date": "<YYYY-MM-DD>",
  "items": [
    {
      "name": "<中文名(原文)>",
      "name_raw": "<原文>",
      "quantity": <数量>,
      "unit_price": <单价或null>,
      "amount": <金额>,
      "item_type": "<item|discount|tax|subtotal>"
    }
  ]
}
"""

_MAX_RETRIES = 2


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class BillParser:
    def __init__(self, llm: LLMManager) -> None:
        self._llm = llm

    async def parse_text(self, user_id: int, text: str) -> BillEntry:
        """
        解析纯文本账单。
        :raises ValueError: 重试耗尽后仍无法解析时。
        """
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": f"请解析以下账单信息：\n\n{text}"},
        ]
        return await self._parse(user_id=user_id, messages=messages, raw_text=text)

    async def parse_image(self, user_id: int, image_base64: str, mime_type: str = "image/jpeg") -> BillEntry:
        """
        解析图片账单（需 Provider 支持 vision，如 gpt-4o / claude-3）。
        :raises ValueError: 解析失败时。
        """
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_base64}"},
                    },
                    {
                        "type": "text",
                        "text": (
                            "请解析这张收据/账单图片中的消费信息。\n"
                            "注意：如果图片包含信用卡票据区域（クレジット売上票），"
                            "请优先从该区域识别加盟店名作为 merchant 字段。"
                        ),
                    },
                ],
            },
        ]
        return await self._parse(user_id=user_id, messages=messages, raw_text="[image]")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _parse(self, user_id: int, messages: list[dict], raw_text: str) -> BillEntry:
        last_error: Optional[str] = None

        for attempt in range(_MAX_RETRIES + 1):
            if last_error:
                messages = messages + [
                    {"role": "assistant", "content": last_error},
                    {
                        "role": "user",
                        "content": (
                            f"上次返回有误：{last_error}\n"
                            "请严格按照 JSON 格式重新返回，不要包含任何说明文字或 Markdown。"
                        ),
                    },
                ]

            try:
                raw_response = await self._llm.chat(messages=messages)
            except RuntimeError as e:
                raise ValueError(f"LLM 服务不可用：{e}") from e

            try:
                data = self._parse_json(raw_response)
            except ValueError as e:
                last_error = raw_response[:300]
                logger.warning("Attempt %d JSON parse failed: %s", attempt + 1, e)
                continue

            if "error" in data:
                raise ValueError(data["error"])

            try:
                return self._build_entry(user_id, data, raw_text)
            except ValueError as e:
                last_error = str(e)
                logger.warning("Attempt %d build_entry failed: %s", attempt + 1, e)
                continue

        raise ValueError(f"账单解析失败，请检查内容后重试。（最后错误：{last_error}）")

    @staticmethod
    def _parse_json(text: str) -> dict:
        text = text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("JSON decode failed: %s | raw: %s", e, text[:200])
            raise ValueError("AI 返回格式异常，无法解析账单。") from e

    @staticmethod
    def _build_entry(user_id: int, data: dict, raw_text: str) -> BillEntry:
        today = date.today().isoformat()

        # ── amount ──
        try:
            amount = float(data.get("amount", 0))
            if amount <= 0:
                raise ValueError("金额必须大于 0")
        except (TypeError, ValueError) as e:
            raise ValueError(f"金额解析失败：{e}") from e

        # ── bill_date ──
        bill_date = str(data.get("bill_date", today))
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", bill_date):
            logger.warning("Invalid bill_date %r, falling back to today", bill_date)
            bill_date = today

        # ── category ──
        category = str(data.get("category", "其他"))
        if category not in VALID_CATEGORIES:
            logger.warning("Invalid category %r, falling back to '其他'", category)
            category = "其他"

        # ── merchant 兜底：禁止 unknown 进库 ──
        merchant = str(data.get("merchant", "")).strip()
        if not merchant or merchant.lower() == "unknown":
            merchant = "未知商家"
            logger.warning("merchant was empty/unknown, set to '未知商家'")

        # ── items ──
        items: list[BillItem] = []
        raw_items = data.get("items") or []
        for idx, it in enumerate(raw_items):
            if not isinstance(it, dict):
                continue
            item_type = str(it.get("item_type", "item"))
            if item_type not in VALID_ITEM_TYPES:
                item_type = "item"
            # subtotal 行不存入数据库，仅跳过
            if item_type == "subtotal":
                continue
            try:
                item_amount = float(it.get("amount", 0))
            except (TypeError, ValueError):
                item_amount = 0.0
            items.append(BillItem(
                name=str(it.get("name", f"商品{idx + 1}"))[:50],
                name_raw=str(it.get("name_raw", ""))[:100],
                quantity=float(it.get("quantity") or 1),
                unit_price=float(it["unit_price"]) if it.get("unit_price") is not None else None,
                amount=item_amount,
                item_type=item_type,
                sort_order=idx,
            ))

        return BillEntry(
            user_id=user_id,
            amount=amount,
            currency=str(data.get("currency", "CNY")).upper(),
            category=category,
            description=str(data.get("description", ""))[:50],
            merchant=merchant,
            bill_date=bill_date,
            raw_text=raw_text,
            items=items,
        )
