# api/routes/ws.py
"""
WebSocket 接入路由
==================

端点：GET /v1/ws

客户端连接示例（Flutter）：
  final channel = WebSocketChannel.connect(
    Uri.parse('ws://host/v1/ws?token=$accessToken'),
  );

连接流程：
  1. 客户端携带 ?token=<JWT> 连接
  2. 服务端验证 token → 回复 auth_ok
  3. 进入双向消息循环（心跳 + 业务推送）
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query, WebSocket

from api.dependencies.client_ip import get_client_ip, ClientIP
from shared.services.container import services

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(default="", description="JWT Access Token"),
):
    """
    WebSocket 长连接入口。

    - token 可通过 query param 传入，也可连接后发送 auth 消息
    - 鉴权失败时服务端主动关闭连接（code 4001）
    """
    # 获取真实 IP（WebSocket 握手请求和 HTTP 请求结构相同）
    client_ip = websocket.headers.get("CF-Connecting-IP") \
        or websocket.headers.get("X-Forwarded-For", "").split(",")[0].strip() \
        or websocket.headers.get("X-Real-IP") \
        or (websocket.client.host if websocket.client else "unknown")

    manager = services.ws_manager
    if manager is None:
        await websocket.accept()
        await websocket.close(code=1013, reason="service unavailable")
        return

    # 握手鉴权
    user_id = await manager.connect(
        websocket,
        token=token or None,
        client_ip=client_ip,
    )
    if user_id is None:
        return  # 鉴权失败，connect() 内部已关闭连接

    # 进入消息接收主循环
    await manager.handle_connection(websocket, user_id)
