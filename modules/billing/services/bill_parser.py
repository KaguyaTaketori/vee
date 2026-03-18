"""
modules/billing/services/bill_parser.py

调用 LLMManager 解析用户发送的收据文本或图片，返回结构化 BillEntry。
业务层不直接接触 LLM，只调用此模块。
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
# System Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """你是一个专业的财务助手，专门从收据/账单文本或图片中提取关键信息。
请严格以 JSON 格式返回，不要包含任何额外说明或 Markdown 代码块。

JSON 格式如下：
{
  "amount": <float，金额，如 128.50>,
  "currency": "<货币代码，如 CNY/USD/JPY>",
  "category": "<消费类别，如：餐饮/交通/购物/娱乐/医疗/其他>",
  "description": "<消费简述，15字以内>",
  "merchant": "<商家名称，未知则填 unknown>",
  "bill_date": "<日期 YYYY-MM-DD 格式，无法确定则填今天>"
}

如果无法从内容中提取有效账单信息，请返回：{"error": "无法识别账单信息"}
"""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class BillParser:
    def __init__(self, llm: LLMManager) -> None:
        self._llm = llm

    async def parse_text(self, user_id: int, text: str) -> BillEntry:
        """
        解析纯文本账单。
        :raises ValueError: LLM 返回错误或 JSON 格式不合法时。
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
        try:
            raw_response = await self._llm.chat(messages=messages, max_tokens=512, temperature=0)
        except RuntimeError as e:
            logger.error("LLM chat failed for user_id=%s: %s", user_id, e)
            raise ValueError(f"AI 服务暂时不可用，请稍后再试。({e})") from e

        data = self._safe_parse_json(raw_response)

        if "error" in data:
            raise ValueError(data["error"])

        return self._build_entry(user_id=user_id, data=data, raw_text=raw_text)

    @staticmethod
    def _safe_parse_json(text: str) -> dict:
        """容错 JSON 解析：去除 markdown 代码块包裹。"""
        text = text.strip()
        # 去掉 ```json ... ``` 或 ``` ... ```
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("JSON decode failed: %s | raw: %s", e, text[:200])
            raise ValueError(f"AI 返回格式异常，无法解析账单。") from e

    @staticmethod
    def _build_entry(user_id: int, data: dict, raw_text: str) -> BillEntry:
        today = date.today().isoformat()

        try:
            amount = float(data.get("amount", 0))
            if amount <= 0:
                raise ValueError("金额必须大于 0")
        except (TypeError, ValueError) as e:
            raise ValueError(f"金额解析失败：{e}") from e

        return BillEntry(
            user_id=user_id,
            amount=amount,
            currency=str(data.get("currency", "CNY")).upper(),
            category=str(data.get("category", "其他")),
            description=str(data.get("description", ""))[:50],
            merchant=str(data.get("merchant", "unknown")),
            bill_date=str(data.get("bill_date", today)),
            raw_text=raw_text,
        )