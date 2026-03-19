"""
api/routes/uploads.py
─────────────────────
图片凭证上传接口。

POST /v1/uploads/receipt
  - JWT 鉴权
  - multipart/form-data，字段名 file
  - 返回 { "receipt_url": "http://host/static/receipts/xxx.jpg" }
"""
from __future__ import annotations

import logging
import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status

from api.auth import require_auth
from api.schemas import UploadReceiptResponse
from shared.services.container import services

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/uploads", tags=["uploads"])

_MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg":  ".jpg",
    "image/png":  ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
}


@router.post(
    "/receipt",
    response_model=UploadReceiptResponse,
    status_code=status.HTTP_201_CREATED,
    summary="上传账单图片凭证",
)
async def upload_receipt(
    user_id: Annotated[int, Depends(require_auth)],
    file: UploadFile = File(..., description="图片文件，支持 jpg/png/webp/heic"),
):
    """
    App 端上传图片凭证。

    - 直接存入正式目录（无需 confirm 步骤，因为用户是主动上传）
    - 返回可供 App 访问的公开 URL
    - URL 存入 BillCreate.receipt_url 后调用 POST /bills 创建账单
    """
    content_type = file.content_type or ""
    ext = _MIME_TO_EXT.get(content_type)

    # 若 content_type 不可信，降级用文件名后缀
    if not ext and file.filename:
        _, raw_ext = os.path.splitext(file.filename)
        ext = raw_ext.lower() if raw_ext else ""

    if not ext:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"不支持的文件类型：{content_type}，仅支持 jpg/png/webp/heic",
        )

    data = await file.read()

    try:
        receipt_url = await services.receipt_storage.save_permanent(data, ext)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("upload_receipt failed for user %s: %s", user_id, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="图片保存失败，请稍后重试",
        )

    logger.info("Receipt uploaded: user_id=%s url=%s", user_id, receipt_url)
    return UploadReceiptResponse(receipt_url=receipt_url)
