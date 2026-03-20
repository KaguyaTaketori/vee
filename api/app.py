"""
api/app.py
──────────
FastAPI 应用工厂。
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .security import create_access_token
from .schemas import TokenRequest, TokenResponse
from .routes.bills import router as bills_router
from .routes.uploads import router as uploads_router
from .routes.auth import router as auth_router
from .routes.me   import router as me_router

logger = logging.getLogger(__name__)

_API_SECRET: str = os.getenv("API_SECRET", "")

def create_app() -> FastAPI:
    app = FastAPI(
        title="Vee Billing API",
        description="手机 App 账单模块 REST API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── CORS ───────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv("API_CORS_ORIGINS", "*").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── 静态文件服务（凭证图片）────────────────────────────────────────────
    # 挂载时确保目录已存在
    from config.settings import UPLOADS_DIR
    receipts_dir = os.path.join(UPLOADS_DIR, "receipts")
    os.makedirs(receipts_dir, exist_ok=True)
    app.mount(
        "/static/receipts",
        StaticFiles(directory=receipts_dir),
        name="receipts",
    )

    # ── 启动时初始化数据库 ──────────────────────────────────────────────────
    @app.on_event("startup")
    async def _startup():
        from modules.billing.database.bills import init_bills_table
        await init_bills_table()

        # FastAPI 进程独立运行时，需要初始化 receipt_storage
        # 若与 Bot 同进程则已由 bootstrap.py 完成，此处幂等
        from shared.services.container import services
        if services.receipt_storage is None:
            from shared.services.receipt_storage import LocalReceiptStorage
            from config.settings import PUBLIC_BASE_URL
            services.receipt_storage = LocalReceiptStorage(
                base_dir=UPLOADS_DIR,
                public_base_url=PUBLIC_BASE_URL,
            )
        logger.info("Vee Billing API started")

    # ── 健康检查 ────────────────────────────────────────────────────────────
    @app.get("/health", tags=["system"])
    async def health():
        return {"status": "ok"}

    # ── Token 换取 ──────────────────────────────────────────────────────────
    @app.post("/auth/token", response_model=TokenResponse, tags=["auth"])
    async def get_token(body: TokenRequest):
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
    app.include_router(uploads_router, prefix="/v1")
    app.include_router(auth_router, prefix="/v1")
    app.include_router(me_router,   prefix="/v1")
    return app


app = create_app()
