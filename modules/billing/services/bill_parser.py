# modules/billing/services/bill_parser.py
"""
变更说明（Bug3 修复）：
1. _SYSTEM_PROMPT 重写关键规则：
   - amount：明确使用「合計/合计/Total/Grand Total」行，禁止用小計/subtotal
   - merchant：保留优先级顺序，增加日文连锁品牌举例，禁止翻译
   - items.name：要求中文翻译后括号保留原文，提高溯源能力
   - 新增"识别置信度"字段：confidence (high/medium/low)，低置信度字段加标注
2. _build_entry：解析 confidence，低置信度在 description 中追加提示
3. 其余逻辑（重试、校验）不变
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
# System Prompt（Bug3 核心修复）
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """你是一个专业的财务助手，专门从收据/账单图片中提取消费信息。

【适用范围】
仅处理实际消费凭证：收据、发票、账单截图、转账记录等。
如果内容是价目表、菜单、广告等，请返回：
  {"error": "这不是消费凭证，请发送实际的收据或账单"}

【金额提取规则（最重要）】
- amount 必须是「合計 / 合计 / Grand Total / Total Amount」行的数字，即最终实付金额
- 禁止使用「小計 / 小计 / Subtotal」行的数字（含税前金额）
- 禁止使用任何单个商品的价格作为 amount
- 如果有信用卡支付金额（クレジット / Credit），以该金额为准
- 金额必须大于 0，单位根据货币符号判断（¥/円=JPY, $=USD, ￥/元=CNY）

【字段说明】
- currency：货币代码 CNY / USD / JPY / HKD / EUR 等
- category：餐饮 / 交通 / 购物 / 娱乐 / 医疗 / 住房 / 水电煤 / 其他（选一个）
- description：本次消费的简短描述，15字以内，必填

【merchant 提取规则（按优先级）】
  ① 收据顶部最大字号的店名（如「サイゼリヤ」「マクドナルド」「星巴克」）
  ② 「クレジット売上票」区域的「加盟店名」字段
  ③ 登録番号上方的株式会社/合同会社名称
  ④ 收据任意处出现的品牌名/店名
- 禁止翻译：直接保留收据原文（如 サイゼリヤ，不要写 萨莉亚）
- 禁止填写 unknown 或空字符串；实在无法识别则填收据最显眼的可读文字

【bill_date 规则】
- 格式 YYYY-MM-DD；无明确日期则填今天；禁止填 unknown

【items 明细规则】
- 有商品明细则必填，无则填空数组 []
- name：中文翻译 + 括号内保留原文，如「辣味炸鸡(辛味チキン)」「烤蜗牛(エスカルゴのオーブン焼き)」
  - 翻译要准确，不得凭空猜测（如「辛味チキン」= 辣味鸡，不是唐扬鸡）
- name_raw：收据原文
- amount：该行金额；折扣为负数
- item_type：item / discount / tax / subtotal
- 按收据从上到下顺序排列

【置信度】
- confidence：对本次整体识别的置信度，填 "high" / "medium" / "low"
  - high：图片清晰，所有字段均可明确读取
  - medium：部分字段（如金额、商家名）模糊，已尽力推断
  - low：图片模糊/遮挡严重，多个关键字段不确定

【返回格式】
严格返回 JSON，不含任何 Markdown 代码块或额外说明：
{
  "amount": <float>,
  "currency": "<货币代码>",
  "category": "<类别>",
  "description": "<描述>",
  "merchant": "<商家原文>",
  "bill_date": "<YYYY-MM-DD>",
  "confidence": "<high|medium|low>",
  "items": [
    {
      "name": "<中文(原文)>",
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
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": f"请解析以下账单信息：\n\n{text}"},
        ]
        return await self._parse(user_id=user_id, messages=messages, raw_text=text)

    async def parse_image(self, user_id: int, image_base64: str, mime_type: str = "image/jpeg") -> BillEntry:
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
                            "请解析这张收据/账单图片的消费信息。\n\n"
                            "特别注意：\n"
                            "1. amount 必须使用「合計」行（最终合计），不是「小計」\n"
                            "2. merchant 使用收据顶部大字店名；若有「クレジット売上票」段落，"
                            "   以「加盟店名」为准，保留原文不翻译\n"
                            "3. items 的 name 格式为「中文翻译(原文)」，翻译要准确"
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
                            "请严格按 JSON 格式重新返回，不含任何 Markdown 或说明文字。"
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

        # ── merchant 兜底 ──
        merchant = str(data.get("merchant", "")).strip()
        if not merchant or merchant.lower() == "unknown":
            merchant = "未知商家"
            logger.warning("merchant was empty/unknown, set to '未知商家'")

        # ── confidence → description 附注 ──
        confidence = str(data.get("confidence", "high")).lower()
        description = str(data.get("description", ""))[:50]
        if confidence == "low" and "⚠️" not in description:
            # 低置信度时在描述末尾提示用户核对
            suffix = "⚠️请核对"
            if len(description) + len(suffix) <= 50:
                description = description + suffix if description else suffix

        # ── items ──
        items: list[BillItem] = []
        raw_items = data.get("items") or []
        for idx, it in enumerate(raw_items):
            if not isinstance(it, dict):
                continue
            item_type = str(it.get("item_type", "item"))
            if item_type not in VALID_ITEM_TYPES:
                item_type = "item"
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

        if confidence != "high":
            logger.info(
                "Bill recognition confidence=%s, amount=%.2f, merchant=%s",
                confidence, amount, merchant,
            )

        return BillEntry(
            user_id=user_id,
            amount=amount,
            currency=str(data.get("currency", "CNY")).upper(),
            category=category,
            description=description,
            merchant=merchant,
            bill_date=bill_date,
            raw_text=raw_text,
            items=items,
        )
