"""Support chat — сервисный слой (карта #92).

Минимальный CRUD над `support_thread` + `support_message`. Без
статусов, audit, тикетов. Thread keyed по (user_id, block) и
создаётся лениво при первом сообщении.

Конвенция: все mutator'ы заканчиваются `await db.commit()`. Перед
commit'ом читаем зависимые атрибуты в локальные переменные —
`feedback_async_sqlalchemy_post_commit`. После commit'а API-роут
может звать realtime/notifications, передавая локалы (а не
объекты).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import User
from app.models.support import (
    SupportBlock,
    SupportMessage,
    SupportSenderKind,
    SupportThread,
)


MAX_PREVIEW = 140


# ─── thread lookup / creation ─────────────────────────────────────────


async def get_thread(
    db: AsyncSession, user_id: int, block: SupportBlock
) -> SupportThread | None:
    res = await db.execute(
        select(SupportThread).where(
            SupportThread.user_id == user_id, SupportThread.block == block
        )
    )
    return res.scalar_one_or_none()


async def get_or_create_thread(
    db: AsyncSession, user_id: int, block: SupportBlock
) -> tuple[SupportThread, bool]:
    """Возвращает (thread, created). UNIQUE гонку обрабатываем
    через IntegrityError → re-SELECT."""
    existing = await get_thread(db, user_id, block)
    if existing is not None:
        return existing, False
    thread = SupportThread(user_id=user_id, block=block)
    db.add(thread)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = await get_thread(db, user_id, block)
        if existing is None:
            raise
        return existing, False
    return thread, True


async def get_thread_by_id(
    db: AsyncSession, thread_id: int
) -> SupportThread | None:
    return await db.get(SupportThread, thread_id)


# ─── messages ─────────────────────────────────────────────────────────


async def list_messages(
    db: AsyncSession,
    thread_id: int,
    before_id: int | None = None,
    limit: int = 50,
) -> list[SupportMessage]:
    """Возвращает последние N (newest first). `before_id` —
    для подгрузки старых: WHERE id < before_id."""
    limit = max(1, min(limit, 200))
    q = (
        select(SupportMessage)
        .where(SupportMessage.thread_id == thread_id)
        .order_by(desc(SupportMessage.id))
        .limit(limit)
    )
    if before_id is not None:
        q = q.where(SupportMessage.id < before_id)
    res = await db.execute(q)
    return list(res.scalars().all())


async def send_message(
    db: AsyncSession,
    thread: SupportThread,
    sender_user_id: int,
    sender_kind: SupportSenderKind,
    body: str,
) -> tuple[SupportMessage, datetime]:
    """Создаёт message, обновляет thread.last_message_at, commit.
    Возвращает (msg, last_message_at) — локалы безопасно использовать
    в роуте после commit'а."""
    msg = SupportMessage(
        thread_id=thread.id,
        sender_user_id=sender_user_id,
        sender_kind=sender_kind,
        body=body,
    )
    db.add(msg)
    await db.flush()
    thread.last_message_at = msg.created_at
    last_at = msg.created_at
    await db.commit()
    await db.refresh(msg)
    return msg, last_at


# ─── read marks ───────────────────────────────────────────────────────


async def mark_read(
    db: AsyncSession,
    thread: SupportThread,
    side: SupportSenderKind,
    up_to_message_id: int | None,
) -> datetime:
    """Помечает прочитанным. `up_to_message_id=None` → используем
    `last_message_at` (или now() если thread пустой). Возвращает
    выставленный timestamp."""
    now_utc = datetime.now(timezone.utc)
    if up_to_message_id is not None:
        msg = await db.get(SupportMessage, up_to_message_id)
        if msg is None or msg.thread_id != thread.id:
            ts = thread.last_message_at or now_utc
        else:
            ts = msg.created_at
    else:
        ts = thread.last_message_at or now_utc

    if side == SupportSenderKind.user:
        thread.user_last_read_at = ts
    else:
        thread.admin_last_read_at = ts
    await db.commit()
    return ts


# ─── read state helpers ───────────────────────────────────────────────


def has_unread_for_user(thread: SupportThread) -> bool:
    """User видит unread если есть admin-сообщения после
    user_last_read_at."""
    if thread.last_message_at is None:
        return False
    if thread.user_last_read_at is None:
        # Есть ли вообще admin-message? Не знаем без отдельного запроса;
        # консервативно — да, если last_message_at от admin'а. Без
        # дополнительного запроса флаг неточен — используем правило
        # «есть последнее сообщение → возможно unread». API делает
        # уточнение через unread_count для admin'а; для user — флаг.
        return True
    return thread.last_message_at > thread.user_last_read_at


async def count_unread_for_admin(
    db: AsyncSession, thread: SupportThread
) -> int:
    """Сообщений от user после admin_last_read_at."""
    q = select(func.count(SupportMessage.id)).where(
        SupportMessage.thread_id == thread.id,
        SupportMessage.sender_kind == SupportSenderKind.user,
    )
    if thread.admin_last_read_at is not None:
        q = q.where(SupportMessage.created_at > thread.admin_last_read_at)
    res = await db.execute(q)
    return int(res.scalar() or 0)


# ─── admin: thread list ──────────────────────────────────────────────


async def list_threads_admin(
    db: AsyncSession,
    before_last_msg_at: datetime | None = None,
    limit: int = 50,
) -> list[tuple[SupportThread, User, str | None, int]]:
    """Список thread'ов для admin'а, отсортированных по
    last_message_at DESC. Возвращает (thread, user, preview,
    unread_count). NULL last_message_at идёт в конец.

    `before_last_msg_at` — пагинация: brings threads с
    last_message_at < cursor."""
    limit = max(1, min(limit, 100))
    # User грузим отдельным запросом (нет relationship на SupportThread).
    q = (
        select(SupportThread)
        .order_by(
            SupportThread.last_message_at.is_(None).asc(),
            desc(SupportThread.last_message_at),
            desc(SupportThread.id),
        )
        .limit(limit)
    )
    if before_last_msg_at is not None:
        q = q.where(SupportThread.last_message_at < before_last_msg_at)
    res = await db.execute(q)
    threads = list(res.scalars().all())
    if not threads:
        return []

    # Load users
    user_ids = list({t.user_id for t in threads})
    res_u = await db.execute(select(User).where(User.id.in_(user_ids)))
    users_by_id = {u.id: u for u in res_u.scalars().all()}

    # Load last message preview per thread (single query)
    thread_ids = [t.id for t in threads]
    last_msg_sub = (
        select(
            SupportMessage.thread_id,
            func.max(SupportMessage.id).label("max_id"),
        )
        .where(SupportMessage.thread_id.in_(thread_ids))
        .group_by(SupportMessage.thread_id)
        .subquery()
    )
    res_lm = await db.execute(
        select(SupportMessage.thread_id, SupportMessage.body)
        .join(
            last_msg_sub,
            SupportMessage.id == last_msg_sub.c.max_id,
        )
    )
    preview_by_thread: dict[int, str] = {}
    for tid, body in res_lm.all():
        preview_by_thread[int(tid)] = truncate(body, MAX_PREVIEW)

    # Unread count per thread
    unread_by_thread: dict[int, int] = {}
    for t in threads:
        unread_by_thread[t.id] = await count_unread_for_admin(db, t)

    return [
        (
            t,
            users_by_id.get(t.user_id),
            preview_by_thread.get(t.id),
            unread_by_thread.get(t.id, 0),
        )
        for t in threads
    ]


def truncate(body: str, limit: int) -> str:
    body = (body or "").strip().replace("\n", " ")
    return body if len(body) <= limit else body[: limit - 1] + "…"
