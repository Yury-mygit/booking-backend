"""Background sweep: переводит resolved тикеты в closed после
`auto_close_days` дней без активности admin'а.

Запускается в FastAPI lifespan (см. main.py). Раз в час смотрит
SupportSettings.auto_close_days и применяет к resolved-тикетам.
Тихо (TG-уведомления юзеру не шлёт — закрытие после resolved'а — это
формальность для архива).

Пишет TicketEvent(auto_closed) + emit_status_change в SSE.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.support import (
    SupportSettings,
    Ticket,
    TicketEventKind,
    TicketStatus,
)
from app.services.support import events as svc_events
from app.services.support import realtime

log = logging.getLogger("support.auto_close")

INTERVAL_SEC = 3600  # 1 hour


async def _sweep_once(db: AsyncSession) -> int:
    settings_row = await db.get(SupportSettings, 1)
    if settings_row is None:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings_row.auto_close_days)

    rows = await db.execute(
        select(Ticket).where(
            Ticket.status == TicketStatus.resolved,
            # last_admin_msg_at либо updated_at — берём последнее общение.
            # Если admin не отвечал вовсе — берём updated_at (по сути та же резолюция).
            Ticket.updated_at < cutoff,
        )
    )
    tickets = list(rows.scalars().all())
    n = 0
    for t in tickets:
        old = t.status
        t.status = TicketStatus.closed
        t.closed_at = datetime.now(timezone.utc)
        t.updated_at = t.closed_at
        await svc_events.log(
            db, ticket_id=t.id, actor_user_id=None,
            kind=TicketEventKind.auto_closed,
            payload={"after_days": settings_row.auto_close_days},
        )
        realtime.emit_status_change(t, old, t.status, actor_user_id=None)
        n += 1

    if n:
        await db.commit()
    return n


async def loop() -> None:
    """Запускается из lifespan main.py. Тихо завершается на CancelledError."""
    log.info("support auto-close loop started, interval=%ss", INTERVAL_SEC)
    try:
        while True:
            try:
                async with AsyncSessionLocal() as db:
                    closed = await _sweep_once(db)
                if closed:
                    log.info("auto-closed %d resolved tickets", closed)
            except Exception:  # noqa: BLE001 — фоновая задача не должна падать
                log.exception("auto_close sweep error")
            await asyncio.sleep(INTERVAL_SEC)
    except asyncio.CancelledError:
        log.info("auto-close loop cancelled")
        raise
