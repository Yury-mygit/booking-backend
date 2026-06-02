"""Audit log helper для TicketEvent.

Каждое мутирующее действие на тикете пишет одну строку (см. enum
`TicketEventKind`). Без этой таблицы невозможно восстановить
кто/когда менял статус, переназначал, добавлял теги — закладываем
с первого дня.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.support import TicketEvent, TicketEventKind


async def log(
    db: AsyncSession,
    *,
    ticket_id: int,
    actor_user_id: int | None,
    kind: TicketEventKind,
    payload: dict[str, Any] | None = None,
) -> TicketEvent:
    """Создать запись аудита.

    actor_user_id=None → system action (cron auto_close и т.п.).
    payload — произвольный JSONB ({from, to, message_id, tag_id, ...}).
    Не делает commit — вызывающая сторона ответственна за транзакцию.
    """
    evt = TicketEvent(
        ticket_id=ticket_id,
        actor_user_id=actor_user_id,
        kind=kind,
        payload=payload or {},
    )
    db.add(evt)
    return evt
