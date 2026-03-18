# modules/billing/services/bill_parser.py
"""
modules/billing/services/bill_parser.py

变更说明：
1. _SYSTEM_PROMPT：
   - 明确拒绝非消费凭证（费率表、菜单、说明书等），要求返回 error
   - category 限定枚举值，包含"水电煤"
   - description 改为必填
   - bill_date 禁止填 unknown，无日期则填今天
2. _build_entry：
   - 对 bill_date 加格式校验，非 YYYY-MM-DD 则兜底为今天
   - 对 category 加枚举校验，不在白名单则归为"其他"（防止脏数据进统计）
3. _parse：
   - 新增最多 2 次自动重试；每次将上次的错误信息附回对话，
     让 LLM 自我修正，而非直接向用户抛错
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Optional

from llm.manager import LLMManager
from modules.billing.services.bill_cache import BillEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 枚举常量（与 Prompt 保持同步）
# ---------------------------------------------------------------------------

VALID_CATEGORIES: frozenset[str] = frozenset(
    {"餐饮", "交通", "购物", "娱乐", "医疗", "住房", "水电煤", "其他"}
)

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """你是一个专业的财务助手，专门从收据/账单中提取消费信息。

【重要规则】
- 只处理实际的消费凭证：收据、发票、账单截图、转账记录等。
- 如果内容是价目表、费率表、菜单、说明书、广告等，不是实际消费记录，请返回：
  {"error": "这不是消费凭证，请发送实际的收据或账单"}
- 如果无法确定实际消费金额，请返回：
  {"error": "无法识别有效的消费金额"}

【字段说明】
- amount：实际支付金额（float），必须大于 0
- currency：货币代码，如 CNY / USD / JPY / HKD / EUR
- category：从以下选项中选择一个：餐饮 / 交通 / 购物 / 娱乐 / 医疗 / 住房 / 水电煤 / 其他
- description：本次消费的简短描述，15字以内，必填，不能为空
- merchant：原样提取来源中出现的商家名称，禁止翻译或转换为其他语言，无法识别则填 unknown
- bill_date：消费日期，YYYY-MM-DD 格式；图片/文字中没有明确日期则填今天的日期，禁止填 unknown

【返回格式】
严格返回 JSON，不包含任何额外说明或 Markdown 代码块：
{
  "amount": <float>,
  "currency": "<货币代码>",
  "category": "<类别>",
  "description": "<描述>",
  "merchant": "<商家名>",
  "bill_date": "<YYYY-MM-DD>"
}
"""

_MAX_RETRIES = 2  # 首次失败后最多再重试 2 次


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
        :param image_base64: base64 编码的图片数据（不含 data:image/... 前缀）。
        :param mime_type: 图片 MIME 类型。
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
                    {"type": "text", "text": "请解析这张收据/账单图片中的消费信息。"},
                ],
            },
        ]
        return await self._parse(user_id=user_id, messages=messages, raw_text="[image]")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _parse(self, user_id: int, messages: list[dict], raw_text: str) -> BillEntry:
        """
        调用 LLM 并解析响应，失败时携带错误信息追加到对话历史后重试。

        重试逻辑：
        - LLM 调用异常（RuntimeError）→ 不重试，直接向用户报告服务不可用。
        - JSON 解析失败或 _build_entry 校验失败 → 最多重试 _MAX_RETRIES 次，
          每次将上次的 assistant 回复和纠错提示追加到 messages，让模型自我修正。
        """
        last_error: Optional[str] = None

        for attempt in range(_MAX_RETRIES + 1):
            # 非首次：将上次失败的 assistant 回复 + 纠错提示追加到对话
            if attempt > 0 and last_error:
                messages = messages + [
                    {"role": "assistant", "content": _last_raw},  # noqa: F821
                    {
                        "role": "user",
                        "content": (
                            f"你的上一次回复有问题：{last_error}\n"
                            "请严格按照格式要求重新返回正确的 JSON，不要包含任何说明文字。"
                        ),
                    },
                ]
                logger.info(
                    "BillParser retry %d/%d for user_id=%s, last_error=%s",
                    attempt, _MAX_RETRIES, user_id, last_error,
                )

            try:
                _last_raw = await self._llm.chat(
                    messages=messages, max_tokens=512, temperature=0
                )
            except RuntimeError as e:
                # LLM 基础设施故障，重试也无意义
                logger.error("LLM chat failed for user_id=%s: %s", user_id, e)
                raise ValueError(f"AI 服务暂时不可用，请稍后再试。({e})") from e

            try:
                data = self._safe_parse_json(_last_raw)
            except ValueError as e:
                last_error = str(e)
                continue

            if "error" in data:
                # LLM 主动返回业务错误（非消费凭证等），不重试
                raise ValueError(data["error"])

            try:
                return self._build_entry(user_id=user_id, data=data, raw_text=raw_text)
            except ValueError as e:
                last_error = str(e)
                continue

        # 所有重试耗尽
        raise ValueError(f"账单解析失败，请检查内容后重新发送。（{last_error}）")

    @staticmethod
    def _safe_parse_json(text: str) -> dict:
        """容错 JSON 解析：去除 markdown 代码块包裹。"""
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

        try:
            amount = float(data.get("amount", 0))
            if amount <= 0:
                raise ValueError("金额必须大于 0")
        except (TypeError, ValueError) as e:
            raise ValueError(f"金额解析失败：{e}") from e

        # bill_date 格式校验：非 YYYY-MM-DD 则兜底为今天，防止 "unknown" 进数据库
        bill_date = str(data.get("bill_date", today))
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", bill_date):
            logger.warning("Invalid bill_date %r, falling back to today", bill_date)
            bill_date = today

        # category 枚举校验：不在白名单则归为"其他"，防止脏数据破坏统计分组
        category = str(data.get("category", "其他"))
        if category not in VALID_CATEGORIES:
            logger.warning("Invalid category %r, falling back to '其他'", category)
            category = "其他"

        return BillEntry(
            user_id=user_id,
            amount=amount,
            currency=str(data.get("currency", "CNY")).upper(),
            category=category,
            description=str(data.get("description", ""))[:50],
            merchant=str(data.get("merchant", "unknown")),
            bill_date=bill_date,
            raw_text=raw_text,
        )
