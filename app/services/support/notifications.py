"""TG-уведомления для support-чата (карта #92).

Контракт упрощённый:
- User написал → DM всем active admin'ам (role=admin OR is_superadmin,
  bot не заблокирован).
- Admin написал → DM user'у — владельцу thread'а.

Вызов: fire-and-forget через `asyncio.create_task(...)` из API-роутов
после `db.commit()`. Своя `AsyncSession` внутри — чтобы не дёргать
expired-attributes из закрытой сессии вызывающего
(`feedback_async_sqlalchemy_post_commit`).

`_send` дублирует httpx-логику из `tg_notifications.py` — осознанно,
как было в v1; общий `services/tg_client.py` появится при унификации.
"""

from __future__ import annotations

import logging
from typing import Sequence

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.models import Lang, User, UserRole
from app.models.support import (
    SupportBlock,
    SupportMessage,
    SupportThread,
)

log = logging.getLogger("support.notify")


# ─── localized templates ──────────────────────────────────────────────


_BTN: dict[Lang, str] = {
    Lang.ru: "Открыть",
    Lang.en: "Open",
    Lang.ky: "Ачуу",
}

_TPL_ADMIN_NEW = {
    Lang.ru: "🆘 Поддержка — новое сообщение от {who} [{block}]\n{preview}",
    Lang.en: "🆘 Support — new message from {who} [{block}]\n{preview}",
    Lang.ky: "🆘 Колдоо — {who} [{block}] жаңы билдирүү жөнөттү\n{preview}",
}

_TPL_USER_REPLY = {
    Lang.ru: "💬 Поддержка\n{preview}",
    Lang.en: "💬 Support\n{preview}",
    Lang.ky: "💬 Колдоо\n{preview}",
}


# ─── helpers ──────────────────────────────────────────────────────────


def _deep_link(block: SupportBlock) -> str:
    base = settings.public_base_app.rstrip("/") + "/"
    return f"{base}?startapp=support_{block.value}"


def _preview(body: str, limit: int = 160) -> str:
    body = (body or "").strip().replace("\n", " ")
    return body if len(body) <= limit else body[: limit - 1] + "…"


def _who(u: User) -> str:
    name = f"{u.first_name or ''} {u.last_name or ''}".strip()
    if not name:
        name = u.username or f"id:{u.telegram_id}"
    return f"{name} [{u.role.value}]"


async def _send(chat_id: int, text: str, deep_link: str, btn: str) -> int:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [
                [{"text": btn, "web_app": {"url": deep_link}}],
            ],
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{settings.tg_bot_token}/sendMessage",
                json=payload,
            )
        return r.status_code
    except httpx.HTTPError as e:
        log.warning("send error: %s", e)
        return 0


async def _set_blocked(db: AsyncSession, user_id: int, blocked: bool) -> None:
    await db.execute(
        update(User)
        .where(User.id == user_id, User.bot_blocked_or_unreachable != blocked)
        .values(bot_blocked_or_unreachable=blocked)
    )
    await db.commit()


async def _deliver(
    db: AsyncSession, recipient: User, text: str, block: SupportBlock
) -> None:
    if recipient.bot_blocked_or_unreachable:
        return
    btn = _BTN.get(recipient.lang, _BTN[Lang.en])
    status = await _send(recipient.telegram_id, text, _deep_link(block), btn)
    if status == 200:
        return
    if status in (400, 403):
        await _set_blocked(db, recipient.id, True)


async def _list_admin_recipients(db: AsyncSession) -> Sequence[User]:
    """role=admin OR is_superadmin, bot не заблокирован."""
    res = await db.execute(
        select(User).where(
            or_(User.role == UserRole.admin, User.is_superadmin.is_(True)),
            User.bot_blocked_or_unreachable.is_(False),
            User.telegram_id.is_not(None),
        )
    )
    return res.scalars().all()


# ─── public API ───────────────────────────────────────────────────────


async def notify_admins_on_user_message(thread_id: int, msg_id: int) -> None:
    """user написал → DM всем admin'ам."""
    if not settings.tg_bot_token:
        return
    async with AsyncSessionLocal() as db:
        thread = await db.get(SupportThread, thread_id)
        if thread is None:
            return
        msg = await db.get(SupportMessage, msg_id)
        if msg is None:
            return
        author = await db.get(User, thread.user_id)
        if author is None:
            return

        recipients = await _list_admin_recipients(db)
        if not recipients:
            return

        for r in recipients:
            tpl = _TPL_ADMIN_NEW.get(r.lang, _TPL_ADMIN_NEW[Lang.en])
            text = tpl.format(
                who=_who(author),
                block=thread.block.value,
                preview=_preview(msg.body),
            )
            await _deliver(db, r, text, thread.block)


async def notify_user_on_admin_message(thread_id: int, msg_id: int) -> None:
    """admin написал → DM владельцу thread'а."""
    if not settings.tg_bot_token:
        return
    async with AsyncSessionLocal() as db:
        thread = await db.get(SupportThread, thread_id)
        if thread is None:
            return
        msg = await db.get(SupportMessage, msg_id)
        if msg is None:
            return
        user = await db.get(User, thread.user_id)
        if user is None:
            return

        tpl = _TPL_USER_REPLY.get(user.lang, _TPL_USER_REPLY[Lang.en])
        text = tpl.format(preview=_preview(msg.body))
        await _deliver(db, user, text, thread.block)
