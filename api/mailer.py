# api/mailer.py
"""
邮件发送（SMTP，异步包装）。
配置项（.env）：
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM
全部缺失时降级为控制台打印（开发模式）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

_SMTP_HOST = os.getenv("SMTP_HOST", "")
_SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
_SMTP_USER = os.getenv("SMTP_USER", "")
_SMTP_PASS = os.getenv("SMTP_PASS", "")
_SMTP_FROM = os.getenv("SMTP_FROM", _SMTP_USER)
_APP_NAME  = os.getenv("APP_NAME", "Vee")


def _send_sync(to: str, subject: str, html: str) -> None:
    if not _SMTP_HOST:
        # 开发模式：直接打印到日志
        logger.info("[DEV MAIL] To=%s | Subject=%s | Body=%s", to, subject, html[:120])
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{_APP_NAME} <{_SMTP_FROM}>"
    msg["To"]      = to
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=15) as s:
        s.ehlo()
        s.starttls()
        s.login(_SMTP_USER, _SMTP_PASS)
        s.sendmail(_SMTP_FROM, to, msg.as_string())


async def send_email(to: str, subject: str, html: str) -> None:
    """在线程池里跑同步 SMTP，不阻塞事件循环。"""
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _send_sync, to, subject, html)
    except Exception as e:
        logger.error("send_email failed to=%s: %s", to, e)


# ── 邮件模板 ───────────────────────────────────────────────────────────────

def _base_template(title: str, body_html: str) -> str:
    return f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px 24px">
      <h2 style="color:#E85D30;margin:0 0 8px">{_APP_NAME}</h2>
      <h3 style="margin:0 0 24px;color:#1a1a1a">{title}</h3>
      {body_html}
      <p style="margin-top:32px;font-size:12px;color:#999">
        此邮件由系统自动发送，请勿回复。
      </p>
    </div>
    """


async def send_activation_code(to: str, code: str) -> None:
    html = _base_template(
        "验证您的邮箱",
        f"""
        <p>您的激活验证码为：</p>
        <div style="font-size:36px;font-weight:bold;letter-spacing:8px;
                    color:#E85D30;margin:16px 0;text-align:center">{code}</div>
        <p style="color:#666">验证码 <strong>10 分钟</strong>内有效，请勿泄露给他人。</p>
        """,
    )
    await send_email(to, f"【{_APP_NAME}】邮箱激活验证码", html)


async def send_reset_code(to: str, code: str) -> None:
    html = _base_template(
        "重置密码",
        f"""
        <p>您申请了密码重置，验证码为：</p>
        <div style="font-size:36px;font-weight:bold;letter-spacing:8px;
                    color:#E85D30;margin:16px 0;text-align:center">{code}</div>
        <p style="color:#666">验证码 <strong>10 分钟</strong>内有效。若非本人操作，请忽略此邮件。</p>
        """,
    )
    await send_email(to, f"【{_APP_NAME}】密码重置验证码", html)
