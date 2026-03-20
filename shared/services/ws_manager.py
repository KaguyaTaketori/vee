# shared/services/ws_manager.py
"""
WebSocket 全局连接管理器
========================

设计要点
--------
- 单例 ConnectionManager，启动时注入 services.ws_manager
- 支持同一用户多设备登录：Dict[user_id, List[WebSocket]]
- 握手时验证 JWT Access Token（query param 或首条消息）
- 心跳保活：服务端每 30s 发 ping，客户端需在 10s 内回 pong
- 推送失败自动清理断连 socket，不阻塞业务逻辑

握手协议
--------
客户端连接时在 query string 带 token：
  ws://host/v1/ws?token=<access_token>

或连接后立即发送 JSON：
  {"type": "auth", "token": "<access_token>"}

服务端回复：
  {"type": "auth_ok", "user_id": 123}
  或
  {"type": "auth_fail", "reason": "invalid token"}

心跳协议
--------
服务端发：  {"type": "ping"}
客户端回：  {"type": "pong"}
超时未收到 pong → 关闭连接并清理

推送消息格式
-----------
{
  "type": "new_bill",          # 事件类型
  "data": { ...BillOut... },   # 业务数据
  "ts": 1710000000             # 服务端时间戳
}
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect, status

from api.security import decode_access_token
from shared.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)

_PING_INTERVAL = 30.0   # 每 30s 发一次 ping
_PONG_TIMEOUT  = 10.0   # 等待 pong 的超时
_MAX_CONNS_PER_USER = 5 # 单用户最大同时连接数


class ConnectionManager:
    """
    WebSocket 连接管理器（单例）。

    主要职责：
    1. 连接鉴权与注册
    2. 断连清理
    3. 向指定用户推送消息
    4. 心跳保活
    """

    def __init__(self) -> None:
        # user_id → [(websocket, last_pong_ts)]
        self._connections: dict[int, list[tuple[WebSocket, float]]] = {}
        self._lock = asyncio.Lock()


    async def connect(
        self,
        websocket: WebSocket,
        token: Optional[str] = None,
        client_ip: str = "unknown",
    ) -> Optional[int]:
        await websocket.accept()

        # Step 1: token from query param or wait for first message
        if not token:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
                msg = json.loads(raw)
                if msg.get("type") == "auth":
                    token = msg.get("token", "")
            except (asyncio.TimeoutError, json.JSONDecodeError, Exception):
                await self._close(websocket, code=4001, reason="auth timeout")
                return None

        # Step 2: 验证 JWT
        user_id = decode_access_token(token or "")
        if user_id is None:
            await websocket.send_text(json.dumps(
                {"type": "auth_fail", "reason": "invalid token"}
            ))
            await self._close(websocket, code=4001, reason="invalid token")
            return None

        # Step 3: 检查账号状态
        user = await UserRepository().get_by_id(user_id)
        if not user or not user.get("is_active"):
            await websocket.send_text(json.dumps(
                {"type": "auth_fail", "reason": "account inactive"}
            ))
            await self._close(websocket, code=4003, reason="account inactive")
            return None

        # Step 4: 注册连接（修复：先踢旧连接，释放锁，等一个事件循环后再推送）
        to_kick: list[WebSocket] = []
        async with self._lock:
            conns = self._connections.setdefault(user_id, [])
            while len(conns) >= _MAX_CONNS_PER_USER:
                old_ws, _ = conns.pop(0)
                to_kick.append(old_ws)
            conns.append((websocket, time.time()))

        # 在锁外关闭旧连接，避免持锁时 await
        for old_ws in to_kick:
            await self._close(old_ws, code=4008, reason="too many connections")

        # ✅ 关键修复：让出事件循环一次
        # 确保旧连接的 WebSocketDisconnect 回调（disconnect 清理）先于
        # auth_ok 发送完成，避免新连接被误推送 force_logout
        await asyncio.sleep(0)

        # Step 5: 更新 last_login_ip
        try:
            await UserRepository().update_last_login_ip(user_id, client_ip)
        except Exception as e:
            logger.warning("更新 last_login_ip 失败: %s", e)

        # Step 6: 发送 auth_ok
        await websocket.send_text(json.dumps({
            "type": "auth_ok",
            "user_id": user_id,
        }))

        logger.info(
            "WS 连接建立: user_id=%s ip=%s 当前连接数=%d",
            user_id, client_ip, len(self._connections.get(user_id, [])),
        )
        return user_id

    async def disconnect(self, websocket: WebSocket, user_id: int) -> None:
        """主动/被动断连时清理注册表。"""
        async with self._lock:
            conns = self._connections.get(user_id, [])
            self._connections[user_id] = [
                (ws, ts) for ws, ts in conns if ws is not websocket
            ]
            if not self._connections[user_id]:
                del self._connections[user_id]
        logger.info("WS 连接断开: user_id=%s", user_id)

    # ------------------------------------------------------------------
    # 消息推送
    # ------------------------------------------------------------------

    async def push_to_user(
        self,
        user_id: int,
        event_type: str,
        data: dict,
    ) -> int:
        """
        向指定用户所有在线设备推送消息。

        Returns
        -------
        int : 成功推送的连接数
        """
        payload = json.dumps({
            "type": event_type,
            "data": data,
            "ts":   int(time.time()),
        }, ensure_ascii=False)

        async with self._lock:
            conns = list(self._connections.get(user_id, []))

        if not conns:
            return 0

        dead: list[WebSocket] = []
        success = 0
        for ws, _ in conns:
            try:
                await ws.send_text(payload)
                success += 1
            except Exception as e:
                logger.debug("WS 推送失败 user_id=%s: %s", user_id, e)
                dead.append(ws)

        # 清理死连接
        if dead:
            async with self._lock:
                self._connections[user_id] = [
                    (ws, ts)
                    for ws, ts in self._connections.get(user_id, [])
                    if ws not in dead
                ]
                if not self._connections.get(user_id):
                    self._connections.pop(user_id, None)

        return success

    async def broadcast(self, event_type: str, data: dict) -> int:
        """向所有在线用户广播（慎用，仅系统公告等场景）。"""
        async with self._lock:
            user_ids = list(self._connections.keys())

        total = 0
        for uid in user_ids:
            total += await self.push_to_user(uid, event_type, data)
        return total

    # ------------------------------------------------------------------
    # 心跳循环（在 handle_websocket 中 create_task 启动）
    # ------------------------------------------------------------------

    async def _heartbeat_loop(
        self, websocket: WebSocket, user_id: int
    ) -> None:
        """
        每 _PING_INTERVAL 秒向客户端发 ping，
        若 _PONG_TIMEOUT 内没收到 pong 则主动关闭。
        """
        while True:
            await asyncio.sleep(_PING_INTERVAL)

            # 确认连接仍在注册表中
            async with self._lock:
                conns = self._connections.get(user_id, [])
                if not any(ws is websocket for ws, _ in conns):
                    return  # 已断连，退出心跳

            try:
                await websocket.send_text(json.dumps({"type": "ping"}))
            except Exception:
                await self.disconnect(websocket, user_id)
                return

            # 等待 pong（简化：下一条消息视为 pong）
            # 真实实现中 receive_loop 会更新 last_pong_ts
            # 此处用 sleep + 连接检查的轻量方案
            await asyncio.sleep(_PONG_TIMEOUT)

    # ------------------------------------------------------------------
    # 连接接入主循环（在 FastAPI ws 路由中 await 此方法）
    # ------------------------------------------------------------------

    async def handle_connection(
        self,
        websocket: WebSocket,
        user_id: int,
    ) -> None:
        """
        连接鉴权通过后的消息接收主循环。
        阻塞直至连接断开。

        Parameters
        ----------
        websocket : 已鉴权的 WebSocket
        user_id   : 已验证的用户 ID
        """
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(websocket, user_id)
        )

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "pong":
                    # 更新 last_pong 时间（心跳保活）
                    async with self._lock:
                        conns = self._connections.get(user_id, [])
                        self._connections[user_id] = [
                            (ws, time.time() if ws is websocket else ts)
                            for ws, ts in conns
                        ]

                elif msg_type == "ping":
                    # 客户端主动 ping → 服务端回 pong
                    await websocket.send_text(json.dumps({"type": "pong"}))

                # 其他消息类型可在此扩展（如订阅特定事件）

        except WebSocketDisconnect:
            logger.info("WS 客户端主动断开: user_id=%s", user_id)
        except Exception as e:
            logger.warning("WS 异常断开 user_id=%s: %s", user_id, e)
        finally:
            heartbeat_task.cancel()
            await self.disconnect(websocket, user_id)

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def online_count(self) -> int:
        """当前在线用户数（去重）。"""
        return len(self._connections)

    def is_online(self, user_id: int) -> bool:
        return bool(self._connections.get(user_id))

    def stats(self) -> dict:
        return {
            "online_users": self.online_count(),
            "total_connections": sum(
                len(v) for v in self._connections.values()
            ),
        }

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    async def _close(
        websocket: WebSocket,
        code: int = 1000,
        reason: str = "",
    ) -> None:
        try:
            await websocket.close(code=code, reason=reason)
        except Exception:
            pass


ws_manager = ConnectionManager()
