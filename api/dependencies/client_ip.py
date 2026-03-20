# api/dependencies/client_ip.py
"""
真实客户端 IP 提取工具
======================

兼容以下反向代理场景：
  - 直接暴露（开发/测试）
  - Nginx：转发 X-Forwarded-For 或 X-Real-IP
  - Cloudflare：转发 CF-Connecting-IP
  - 多层代理：X-Forwarded-For 取第一个非私有地址

使用方式
--------
在路由函数中注入：

    from api.dependencies.client_ip import ClientIP, get_client_ip

    @router.post("/login")
    async def login(
        body: LoginRequest,
        client_ip: ClientIP = Depends(get_client_ip),
    ):
        # client_ip.address  →  str，如 "1.2.3.4"
        # client_ip.source   →  str，如 "CF-Connecting-IP"（调试用）
        await repo.update_last_login_ip(user_id, client_ip.address)

对于 Telegram Bot 触发的入库操作，直接传入 TELEGRAM_BOT_IP 常量：
    IP_FROM_TELEGRAM = "Telegram"
"""
from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Request

logger = logging.getLogger(__name__)

# Bot 侧触发入库时使用的固定标记
IP_FROM_TELEGRAM = "Telegram"

# 私有/保留地址范围（不应作为真实来源 IP）
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_private(ip_str: str) -> bool:
    """判断 IP 是否为私有/保留地址（无法代表真实客户端）。"""
    try:
        addr = ipaddress.ip_address(ip_str.strip())
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        return False


def _first_public_ip(forwarded_for: str) -> Optional[str]:
    """
    从 X-Forwarded-For 头部取第一个非私有 IP。

    X-Forwarded-For 格式：client, proxy1, proxy2
    最左侧是最原始的客户端 IP（可能被伪造，但在信任代理的环境下是准确的）。
    """
    for candidate in forwarded_for.split(","):
        candidate = candidate.strip()
        if candidate and not _is_private(candidate):
            return candidate
    # 全是私有地址时（如内网 Nginx → 应用），退回第一个
    parts = [p.strip() for p in forwarded_for.split(",") if p.strip()]
    return parts[0] if parts else None


@dataclass(frozen=True)
class ClientIP:
    """封装客户端 IP 及其来源头部（用于调试审计）。"""
    address: str          # 最终使用的 IP 字符串
    source: str           # 来源描述，如 "CF-Connecting-IP" / "X-Real-IP" / "direct"


async def get_client_ip(request: Request) -> ClientIP:
    """
    FastAPI 依赖：按优先级提取真实客户端 IP。

    优先级：
      1. CF-Connecting-IP     （Cloudflare 专用，最可信）
      2. X-Forwarded-For      （Nginx / 通用反向代理）
      3. X-Real-IP            （Nginx realip 模块）
      4. request.client.host  （直连，开发环境 fallback）
    """
    headers = request.headers

    # 1. Cloudflare
    cf_ip = headers.get("CF-Connecting-IP", "").strip()
    if cf_ip:
        return ClientIP(address=cf_ip, source="CF-Connecting-IP")

    # 2. X-Forwarded-For（取第一个公网地址）
    xff = headers.get("X-Forwarded-For", "").strip()
    if xff:
        ip = _first_public_ip(xff)
        if ip:
            return ClientIP(address=ip, source="X-Forwarded-For")

    # 3. X-Real-IP
    xri = headers.get("X-Real-IP", "").strip()
    if xri:
        return ClientIP(address=xri, source="X-Real-IP")

    # 4. 直连
    direct = (request.client.host if request.client else "unknown")
    return ClientIP(address=direct, source="direct")


# 方便在不需要 Request 对象的地方直接取地址字符串
async def get_client_ip_str(
    client_ip: ClientIP = Depends(get_client_ip),
) -> str:
    return client_ip.address
