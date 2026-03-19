"""
api/app.py
──────────
FastAPI 应用工厂。

启动方式：
    uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload

与 Telegram Bot 共存：
    - Bot 独立进程（python vee.py）
    - API 独立进程（uvicorn api.app:app ...）
    - 共享同一个 SQLite 文件（DB_PATH）
    - 注意：SQLite WAL 模式下多进程读写安全，默认已开启

环境变量（在现有 .env 追加）：
    API_JWT_SECRET=<随机长字符串>
    API_SECRET=<换 token 时的密钥，仅后台使用>
    API_TOKEN_TTL=2592000   # 30天，单位秒
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from .auth import create_access_token, _API_SECRET
from .schemas import TokenRequest, TokenResponse
from .routes.bills import router as bills_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Vee Billing API",
        description="手机 App 账单模块 REST API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── CORS（开发阶段放开，上线前收紧 origins）────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv("API_CORS_ORIGINS", "*").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── 启动时初始化数据库 ──────────────────────────────────────────────────
    @app.on_event("startup")
    async def _startup():
        from modules.billing.database.bills import init_bills_table
        await init_bills_table()
        logger.info("Vee Billing API started")

    # ── 健康检查 ────────────────────────────────────────────────────────────
    @app.get("/health", tags=["system"])
    async def health():
        return {"status": "ok"}

    # ── Token 换取（简单 secret 方案）──────────────────────────────────────
    @app.post("/auth/token", response_model=TokenResponse, tags=["auth"])
    async def get_token(body: TokenRequest):
        """
        用 user_id + API_SECRET 换取 JWT。
        生产环境建议改为 Telegram Login Widget 验证。
        """
        if not _API_SECRET:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Auth not configured (API_SECRET not set)",
            )
        if body.secret != _API_SECRET:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid secret",
            )
        token = create_access_token(body.user_id)
        return TokenResponse(access_token=token)

    # ── 路由注册 ────────────────────────────────────────────────────────────
    app.include_router(bills_router, prefix="/v1")

    return app


# uvicorn api.app:app
app = create_app()
