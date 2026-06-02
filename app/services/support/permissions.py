"""Support-specific permission deps.

Три независимые оси (по карте):
- `current_user` (`core.deps`) — любой залогиненный, для user-side endpoints.
- `require_superadmin` (`core.deps`) — управление roster агентов, settings.
- `require_support_agent` — работа с тикетами в admin-блоке.
- `require_support_lead` — lead-only действия (transfer, escalate, mass).

Активный агент = строка в `support_agent` с `removed_at IS NULL`.
"""

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext, current_user
from app.core.exceptions import APIError
from app.models.support import SupportAgent


async def require_support_agent(
    ctx: AuthContext = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    row = await db.execute(
        select(SupportAgent.id)
        .where(
            SupportAgent.user_id == ctx.user.id,
            SupportAgent.removed_at.is_(None),
        )
        .limit(1)
    )
    if row.scalar() is None:
        raise APIError(403, "forbidden", "Support agent access required")
    return ctx


async def require_support_lead(
    ctx: AuthContext = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    row = await db.execute(
        select(SupportAgent.id)
        .where(
            SupportAgent.user_id == ctx.user.id,
            SupportAgent.removed_at.is_(None),
            SupportAgent.is_lead.is_(True),
        )
        .limit(1)
    )
    if row.scalar() is None:
        raise APIError(403, "forbidden", "Support lead access required")
    return ctx


async def is_support_agent(db: AsyncSession, user_id: int) -> bool:
    """Не-depends helper: проверка для бизнес-логики (например, при
    распределении уведомлений между агентами)."""
    row = await db.execute(
        select(SupportAgent.id)
        .where(
            SupportAgent.user_id == user_id,
            SupportAgent.removed_at.is_(None),
        )
        .limit(1)
    )
    return row.scalar() is not None
