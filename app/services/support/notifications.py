"""TG-уведомления для support.

Контракт:
- User написал в тикет / создал тикет → личка `assignee` (если есть),
  иначе всем активным `is_lead=true`, иначе всем активным agent'ам.
- Agent написал public → личка user'у (по `users.telegram_id`).
- Agent написал internal → молча (между агентами — отдельная задача v1.5).
- Status change (resolved/closed/reopened) → личка user'у.

Вызов: fire-and-forget через `asyncio.create_task(...)` из API-роутов
**после `db.commit()`**. Своя `AsyncSession` внутри — чтобы не дёргать
expired-attributes из закрытой сессии вызывающего.

`_send` дублирует httpx-логику из `tg_notifications.py` (10 строк) —
осознанно, чтобы не делать сейчас рефакторинг рабочего модуля под
свою сторону. Когда оба домена пойдут на унификацию tg-уровня — будет
общий `services/tg_client.py`.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.models import Lang, User
from app.models.support import SupportAgent, Ticket, TicketMessage, TicketStatus

log = logging.getLogger("support.notify")


# ─── localized templates ────────────────────────────────────────────


_BTN: dict[Lang, str] = {
    Lang.ru: "Открыть",
    Lang.en: "Open",
    Lang.ky: "Ачуу",
}

_TPL_AGENT_NEW = {  # юзер → агент
    Lang.ru: "🆘 Поддержка: новое обращение {num}\nОт: {who}\n{preview}",
    Lang.en: "🆘 Support: new ticket {num}\nFrom: {who}\n{preview}",
    Lang.ky: "🆘 Колдоо: жаңы билдирүү {num}\nКимден: {who}\n{preview}",
}

_TPL_USER_REPLY = {  # агент → юзер
    Lang.ru: "💬 Поддержка по {num}\n{preview}",
    Lang.en: "💬 Support — {num}\n{preview}",
    Lang.ky: "💬 Колдоо — {num}\n{preview}",
}

_TPL_STATUS = {
    (Lang.ru, TicketStatus.resolved): "✅ Обращение {num} помечено решённым.",
    (Lang.ru, TicketStatus.closed): "🗄 Обращение {num} закрыто.",
    (Lang.ru, TicketStatus.pending_admin): "🔄 Обращение {num} переоткрыто.",
    (Lang.en, TicketStatus.resolved): "✅ Ticket {num} marked resolved.",
    (Lang.en, TicketStatus.closed): "🗄 Ticket {num} closed.",
    (Lang.en, TicketStatus.pending_admin): "🔄 Ticket {num} reopened.",
    (Lang.ky, TicketStatus.resolved): "✅ {num} билдирүүсү чечилди.",
    (Lang.ky, TicketStatus.closed): "🗄 {num} билдирүүсү жабылды.",
    (Lang.ky, TicketStatus.pending_admin): "🔄 {num} билдирүүсү кайра ачылды.",
}


# ─── low-level send ────────────────────────────────────────────────


def _deep_link(start_param: str) -> str:
    base = settings.public_base_app.rstrip("/") + "/"
    return f"{base}?startapp={start_param}"


def _preview(body: str, limit: int = 160) -> str:
    body = (body or "").strip().replace("\n", " ")
    return body if len(body) <= limit else body[: limit - 1] + "…"


def _who(u: User) -> str:
    name = f"{u.first_name or ''} {u.last_name or ''}".strip()
    if not name:
        name = u.username or f"id:{u.telegram_id}"
    role = u.role.value
    return f"{name} [{role}]"


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


async def _deliver(db: AsyncSession, recipient: User, text: str, start_param: str) -> None:
    if recipient.bot_blocked_or_unreachable:
        return  # уважение к пометке; SPA баннер просит /start
    btn = _BTN.get(recipient.lang, _BTN[Lang.en])
    status = await _send(recipient.telegram_id, text, _deep_link(start_param), btn)
    if status == 200:
        return
    if status in (400, 403):
        await _set_blocked(db, recipient.id, True)


# ─── recipient resolvers ───────────────────────────────────────────


async def _agents_recipients(db: AsyncSession, ticket: Ticket) -> list[User]:
    """assignee → leads → все активные agent'ы."""
    if ticket.assignee_id is not None:
        u = await db.get(User, ticket.assignee_id)
        return [u] if u else []
    # active leads first
    leads = (await db.execute(
        select(User)
        .join(SupportAgent, SupportAgent.user_id == User.id)
        .where(SupportAgent.removed_at.is_(None), SupportAgent.is_lead.is_(True))
    )).scalars().all()
    if leads:
        return list(leads)
    # fallback: всем активным
    all_agents = (await db.execute(
        select(User)
        .join(SupportAgent, SupportAgent.user_id == User.id)
        .where(SupportAgent.removed_at.is_(None))
    )).scalars().all()
    return list(all_agents)


# ─── public API ────────────────────────────────────────────────────


async def notify_new_user_message(ticket_id: int, msg_id: int) -> None:
    """user-msg (или новый тикет) → agent'ам."""
    if not settings.tg_bot_token:
        return
    async with AsyncSessionLocal() as db:
        ticket = await db.get(Ticket, ticket_id)
        if ticket is None:
            return
        msg = await db.get(TicketMessage, msg_id)
        if msg is None:
            return
        author = await db.get(User, ticket.user_id)
        if author is None:
            return

        recipients = await _agents_recipients(db, ticket)
        if not recipients:
            return

        for r in recipients:
            text = _TPL_AGENT_NEW.get(r.lang, _TPL_AGENT_NEW[Lang.en]).format(
                num=ticket.number, who=_who(author), preview=_preview(msg.body),
            )
            await _deliver(db, r, text, start_param=f"support_{ticket.number}")


async def notify_user_reply(ticket_id: int, msg_id: int) -> None:
    """agent public message → юзеру. Internal — не зовём отсюда."""
    if not settings.tg_bot_token:
        return
    async with AsyncSessionLocal() as db:
        ticket = await db.get(Ticket, ticket_id)
        if ticket is None:
            return
        msg = await db.get(TicketMessage, msg_id)
        if msg is None or msg.is_internal:
            return
        user = await db.get(User, ticket.user_id)
        if user is None:
            return

        text = _TPL_USER_REPLY.get(user.lang, _TPL_USER_REPLY[Lang.en]).format(
            num=ticket.number, preview=_preview(msg.body),
        )
        await _deliver(db, user, text, start_param=f"support_{ticket.number}")


async def notify_user_status_change(ticket_id: int, new_status_value: str) -> None:
    """resolved / closed / reopened (pending_admin) → юзеру."""
    if not settings.tg_bot_token:
        return
    try:
        new_status = TicketStatus(new_status_value)
    except ValueError:
        return
    # Только видимые юзеру статусы.
    if new_status not in (TicketStatus.resolved, TicketStatus.closed, TicketStatus.pending_admin):
        return

    async with AsyncSessionLocal() as db:
        ticket = await db.get(Ticket, ticket_id)
        if ticket is None:
            return
        user = await db.get(User, ticket.user_id)
        if user is None:
            return

        # pending_admin = reopen — слать только если это после resolved/closed
        # (а не просто перевод open → pending_admin при ответе юзера, который
        # пользователь и так инициировал).
        if new_status == TicketStatus.pending_admin and ticket.last_admin_msg_at is None:
            return

        key = (user.lang, new_status)
        tpl = _TPL_STATUS.get(key) or _TPL_STATUS.get((Lang.en, new_status))
        if tpl is None:
            return
        text = tpl.format(num=ticket.number)
        await _deliver(db, user, text, start_param=f"support_{ticket.number}")
