"""TG-уведомления о новых чат-сообщениях.

Карта: open_cards/cards/booking/feature/2026-05-28-client-hotel-chat.md
(Этап 4). Fire-and-forget: вызывается из chat.append_message через
asyncio.create_task — REST-ответ не ждёт TG-доставки.

Логика:
- сообщение от клиента → шлём всем юзерам с правами chat_with_clients
  на этом отеле (owner + соответствующий staff);
- сообщение от отеля → шлём клиенту.
- inline-кнопка web_app с deep-link через start_param.

Дедуп: in-memory dict {(recipient_user_id, thread_id): monotonic_ts},
TTL 30с (карта 4.4). При рестарте теряется — приемлемо.

bot_blocked_or_unreachable: ставим True при 403, сбрасываем при успехе.
SPA читает поле (Этап 5) и показывает баннер «нажмите Start у @<bot>».
"""
import time
from typing import Iterable

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from sqlalchemy import and_, or_

from app.models.models import (
    ChatMessage,
    ChatSenderKind,
    ChatThread,
    Hotel,
    PartnerRole,
    PartnerStaff,
    PartnerStaffRole,
    User,
)

_DEDUP_TTL_SEC = 30
_DEDUP_MAX_ENTRIES = 4096
_dedup: dict[tuple[int, int], float] = {}


def _should_skip(recipient_user_id: int, thread_id: int) -> bool:
    key = (recipient_user_id, thread_id)
    now = time.monotonic()
    last = _dedup.get(key)
    if last is not None and now - last < _DEDUP_TTL_SEC:
        return True
    _dedup[key] = now
    if len(_dedup) > _DEDUP_MAX_ENTRIES:
        cutoff = now - _DEDUP_TTL_SEC
        for k in [k for k, t in _dedup.items() if t < cutoff]:
            _dedup.pop(k, None)
    return False


# Локализация. Lang — User.lang получателя.
_TPL_TO_CLIENT = {
    "ru": "💬 {hotel}\n{preview}",
    "ky": "💬 {hotel}\n{preview}",
    "en": "💬 {hotel}\n{preview}",
}
_TPL_TO_HOTEL = {
    "ru": "💬 Сообщение от {client}\n{preview}",
    "ky": "💬 {client} жазды\n{preview}",
    "en": "💬 Message from {client}\n{preview}",
}
_BTN = {"ru": "Открыть чат", "ky": "Чатты ачуу", "en": "Open chat"}


def _preview(body: str) -> str:
    body = body.replace("\n", " ").strip()
    return body if len(body) <= 120 else body[:119] + "…"


def _hotel_name(hotel: Hotel, lang: str) -> str:
    if lang == "ky" and hotel.name_ky:
        return hotel.name_ky
    if lang == "en" and hotel.name_en:
        return hotel.name_en
    return hotel.name_ru


def _client_name(user: User) -> str:
    name = (user.first_name or "").strip()
    last = (user.last_name or "").strip()
    if name and last:
        return f"{name} {last[0]}."
    return name or last or "клиент"


def _lang_str(user_lang) -> str:
    s = user_lang.value if hasattr(user_lang, "value") else str(user_lang)
    return s if s in ("ru", "ky", "en") else "ru"


def _deep_link(start_param: str) -> str:
    base = settings.public_base_app.rstrip("/") + "/"
    return f"{base}?startapp={start_param}"


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
        print(f"[tg_notify] send error: {e}")
        return 0


async def _set_blocked(db: AsyncSession, user_id: int, blocked: bool) -> None:
    await db.execute(
        update(User)
        .where(User.id == user_id, User.bot_blocked_or_unreachable != blocked)
        .values(bot_blocked_or_unreachable=blocked)
    )
    await db.commit()


async def _notify_one(
    db: AsyncSession,
    recipient: User,
    text: str,
    start_param: str,
    thread_id: int,
) -> None:
    if _should_skip(recipient.id, thread_id):
        return
    lang = _lang_str(recipient.lang)
    status = await _send(recipient.telegram_id, text, _deep_link(start_param), _BTN[lang])
    if status == 200:
        if recipient.bot_blocked_or_unreachable:
            await _set_blocked(db, recipient.id, False)
    elif status in (400, 403):
        # 403 = Forbidden: bot was blocked / 400 = chat not found (/start не нажат).
        if not recipient.bot_blocked_or_unreachable:
            await _set_blocked(db, recipient.id, True)


async def _hotel_recipients(db: AsyncSession, hotel: Hotel) -> list[User]:
    """Owner + staff с perm_chat_with_clients=true."""
    owner = (
        await db.execute(select(User).where(User.id == hotel.owner_user_id))
    ).scalar_one_or_none()
    # Effective chat_with_clients = explicit ps.perm=true OR (ps.perm IS NULL
    # AND ЛЮБАЯ из ролей staff'а даёт perm=true). NULL/false override на
    # staff отключает наследование от любых ролей. DISTINCT — junction
    # размножает ряды по количеству ролей.
    staff_users = (
        await db.execute(
            select(User)
            .distinct()
            .join(PartnerStaff, PartnerStaff.staff_user_id == User.id)
            .outerjoin(PartnerStaffRole, PartnerStaffRole.staff_id == PartnerStaff.id)
            .outerjoin(PartnerRole, PartnerRole.id == PartnerStaffRole.role_id)
            .where(
                PartnerStaff.owner_user_id == hotel.owner_user_id,
                or_(
                    PartnerStaff.perm_chat_with_clients.is_(True),
                    and_(
                        PartnerStaff.perm_chat_with_clients.is_(None),
                        PartnerRole.perm_chat_with_clients.is_(True),
                    ),
                ),
            )
        )
    ).scalars().all()
    seen: set[int] = set()
    out: list[User] = []
    for u in [owner, *staff_users]:
        if u is None or u.id in seen:
            continue
        seen.add(u.id)
        out.append(u)
    return out


async def notify_chat_message(thread_id: int, msg_id: int) -> None:
    """Fire-and-forget entrypoint. Открывает свою сессию — вызывается из
    asyncio.create_task, исходная сессия в момент исполнения может быть закрыта.
    Все ошибки логируем print'ом, не пробрасываем (REST-ответ уже отправлен).
    """
    if not settings.tg_bot_token:
        return
    if not settings.chat_tg_notifications_enabled:
        return
    try:
        async with AsyncSessionLocal() as db:
            msg = (
                await db.execute(select(ChatMessage).where(ChatMessage.id == msg_id))
            ).scalar_one_or_none()
            if msg is None:
                return
            thread = (
                await db.execute(select(ChatThread).where(ChatThread.id == thread_id))
            ).scalar_one_or_none()
            if thread is None:
                return
            hotel = (
                await db.execute(select(Hotel).where(Hotel.id == thread.hotel_id))
            ).scalar_one_or_none()
            if hotel is None:
                return

            preview = _preview(msg.body)
            if msg.sender_kind == ChatSenderKind.client:
                # → шлём отелю (owner + staff)
                client_user = (
                    await db.execute(
                        select(User).where(User.id == thread.client_user_id)
                    )
                ).scalar_one_or_none()
                client_label = _client_name(client_user) if client_user else "клиент"
                start_param = f"p_chat_{thread_id}"
                recipients = await _hotel_recipients(db, hotel)
                for r in recipients:
                    lang = _lang_str(r.lang)
                    text = _TPL_TO_HOTEL[lang].format(client=client_label, preview=preview)
                    await _notify_one(db, r, text, start_param, thread_id)
            else:
                # → шлём клиенту
                client_user = (
                    await db.execute(
                        select(User).where(User.id == thread.client_user_id)
                    )
                ).scalar_one_or_none()
                if client_user is None:
                    return
                lang = _lang_str(client_user.lang)
                text = _TPL_TO_CLIENT[lang].format(
                    hotel=_hotel_name(hotel, lang), preview=preview
                )
                start_param = f"chat_{thread_id}"
                await _notify_one(db, client_user, text, start_param, thread_id)
    except Exception as e:
        print(f"[tg_notify] notify_chat_message failed: {e!r}")
