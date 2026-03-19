# shared/services/receipt_storage.py
"""
凭证图片存储抽象层。

当前启用：LocalReceiptStorage（本地磁盘 + FastAPI 静态文件）
预留扩展：TelegramReceiptStorage（TODO，接口已定义）

切换存储只需在 bootstrap.py 修改注入的实现类，上层代码零改动。
"""
from __future__ import annotations

import logging
import os
import shutil
import uuid
from typing import AsyncIterator, Protocol, runtime_checkable

import aiofiles

logger = logging.getLogger(__name__)

# 允许的图片格式
_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
# 单张图片最大体积：10MB
_MAX_FILE_BYTES = 10 * 1024 * 1024


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class ReceiptStorage(Protocol):
    """
    凭证存储协议。所有实现必须满足此接口。

    identifier
    ----------
    LocalReceiptStorage  → 文件名，如 "abc123.jpg"
    TelegramReceiptStorage → Telegram file_id
    """

    async def save_tmp(self, data: bytes, ext: str, hint: str = "") -> str:
        """
        将图片保存到临时区域。

        Parameters
        ----------
        data : 图片二进制内容
        ext  : 文件扩展名，如 ".jpg"
        hint : 可选的关联标识（如 cache_id），便于关联清理

        Returns
        -------
        tmp_identifier : 临时标识符，传给 confirm() 使用
        """
        ...

    async def confirm(self, tmp_identifier: str) -> str:
        """
        将临时文件移动到正式存储区域。

        Returns
        -------
        receipt_url : 可供 App 访问的永久 URL 或标识符
        """
        ...

    async def delete_tmp(self, tmp_identifier: str) -> None:
        """删除临时文件（用户取消 / 缓存过期时调用）。"""
        ...

    async def delete(self, identifier: str) -> None:
        """删除正式存储中的文件（账单删除时调用）。"""
        ...


# ---------------------------------------------------------------------------
# 本地磁盘实现
# ---------------------------------------------------------------------------

class LocalReceiptStorage:
    """
    本地磁盘存储实现。

    目录结构
    --------
    {base_dir}/
      pending/    ← 临时文件（用户确认前）
      receipts/   ← 正式文件（用户确认后，FastAPI serve）
    """

    def __init__(self, base_dir: str, public_base_url: str) -> None:
        """
        Parameters
        ----------
        base_dir        : 存储根目录，如 "/app/uploads"
        public_base_url : 对外访问的 URL 前缀，如 "http://127.0.0.1:8000/static"
        """
        self._pending_dir = os.path.join(base_dir, "pending")
        self._receipts_dir = os.path.join(base_dir, "receipts")
        self._public_base_url = public_base_url.rstrip("/")

        os.makedirs(self._pending_dir, exist_ok=True)
        os.makedirs(self._receipts_dir, exist_ok=True)
        logger.info(
            "LocalReceiptStorage ready: pending=%s receipts=%s",
            self._pending_dir, self._receipts_dir,
        )

    # ── 校验 ──────────────────────────────────────────────────────────────

    @staticmethod
    def validate(data: bytes, ext: str) -> None:
        """校验文件大小和格式，不合法则抛 ValueError。"""
        if len(data) > _MAX_FILE_BYTES:
            raise ValueError(
                f"图片大小超过限制（最大 {_MAX_FILE_BYTES // 1024 // 1024}MB）"
            )
        if ext.lower() not in _ALLOWED_EXTENSIONS:
            raise ValueError(
                f"不支持的图片格式：{ext}，仅支持 {_ALLOWED_EXTENSIONS}"
            )

    # ── ReceiptStorage 实现 ────────────────────────────────────────────────

    async def save_tmp(self, data: bytes, ext: str, hint: str = "") -> str:
        """
        保存到 pending/ 目录。

        Returns
        -------
        tmp_identifier : "pending/{filename}"
        """
        self.validate(data, ext)
        filename = f"{hint}_{uuid.uuid4().hex}{ext}" if hint else f"{uuid.uuid4().hex}{ext}"
        path = os.path.join(self._pending_dir, filename)
        async with aiofiles.open(path, "wb") as f:
            await f.write(data)
        logger.debug("ReceiptStorage: saved tmp %s (%d bytes)", path, len(data))
        return f"pending/{filename}"

    async def confirm(self, tmp_identifier: str) -> str:
        """
        将 pending/ 文件移动到 receipts/。

        Returns
        -------
        receipt_url : 完整的公开访问 URL
        """
        if not tmp_identifier.startswith("pending/"):
            # 已经是正式 URL 或空字符串，直接返回
            return tmp_identifier

        filename = tmp_identifier[len("pending/"):]
        src = os.path.join(self._pending_dir, filename)
        dst = os.path.join(self._receipts_dir, filename)

        if not os.path.exists(src):
            logger.warning("ReceiptStorage.confirm: tmp file not found: %s", src)
            return ""

        shutil.move(src, dst)
        url = f"{self._public_base_url}/receipts/{filename}"
        logger.info("ReceiptStorage: confirmed %s → %s", src, url)
        return url

    async def delete_tmp(self, tmp_identifier: str) -> None:
        if not tmp_identifier.startswith("pending/"):
            return
        filename = tmp_identifier[len("pending/"):]
        path = os.path.join(self._pending_dir, filename)
        _safe_remove(path)

    async def delete(self, identifier: str) -> None:
        """
        identifier 可能是完整 URL 或文件名。
        兼容两种格式：
          - "http://host/static/receipts/abc.jpg"
          - "receipts/abc.jpg"
        """
        if not identifier:
            return
        # 从 URL 中提取文件名
        filename = identifier.split("/receipts/")[-1] if "/receipts/" in identifier else identifier
        path = os.path.join(self._receipts_dir, filename)
        _safe_remove(path)

    async def save_permanent(self, data: bytes, ext: str) -> str:
        """
        直接保存到正式目录（App 上传图片时使用，无需 confirm 步骤）。

        Returns
        -------
        receipt_url : 完整的公开访问 URL
        """
        self.validate(data, ext)
        filename = f"{uuid.uuid4().hex}{ext}"
        path = os.path.join(self._receipts_dir, filename)
        async with aiofiles.open(path, "wb") as f:
            await f.write(data)
        url = f"{self._public_base_url}/receipts/{filename}"
        logger.info("ReceiptStorage: saved permanent %s (%d bytes)", path, len(data))
        return url


# ---------------------------------------------------------------------------
# Telegram 实现（预留）
# ---------------------------------------------------------------------------

class TelegramReceiptStorage:
    """
    TODO: Telegram 频道存储实现。

    设计要点（待实现时参考）：
    - save_tmp    → 暂存 bytes 到本地临时文件（与 Local 相同）
    - confirm     → 调用 bot.send_document(CHANNEL_ID, file) → 取回 file_id
                    返回值格式：  "tg://{file_id}"
    - delete_tmp  → 删除本地临时文件
    - delete      → Telegram 不支持删除，记录日志即可
    - save_permanent → 直接发送到频道，返回 "tg://{file_id}"

    App 访问图片时需通过代理端点：
    GET /v1/receipts/proxy/{file_id}
      └── 服务端用 bot.get_file(file_id) 取流，Token 不外泄
    """

    def __init__(self, bot, channel_id: int, tmp_dir: str) -> None:
        self._bot = bot
        self._channel_id = channel_id
        self._tmp_dir = tmp_dir
        os.makedirs(tmp_dir, exist_ok=True)

    async def save_tmp(self, data: bytes, ext: str, hint: str = "") -> str:
        raise NotImplementedError("TelegramReceiptStorage.save_tmp: TODO")

    async def confirm(self, tmp_identifier: str) -> str:
        raise NotImplementedError("TelegramReceiptStorage.confirm: TODO")

    async def delete_tmp(self, tmp_identifier: str) -> None:
        raise NotImplementedError("TelegramReceiptStorage.delete_tmp: TODO")

    async def delete(self, identifier: str) -> None:
        logger.info("TelegramReceiptStorage.delete: Telegram 不支持删除，identifier=%s", identifier)

    async def save_permanent(self, data: bytes, ext: str) -> str:
        raise NotImplementedError("TelegramReceiptStorage.save_permanent: TODO")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _safe_remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.debug("ReceiptStorage: deleted %s", path)
    except OSError as e:
        logger.warning("ReceiptStorage: failed to delete %s: %s", path, e)
