"""Audit log helper.

Call `await audit(db, ctx, owner_user_id, action, ...)` AFTER db.commit().
The helper itself does not commit — caller decides whether to flush along
with the main change or commit separately.
"""
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AuditLog

if TYPE_CHECKING:
    from app.core.deps import AuthContext


async def audit(
    db: AsyncSession,
    ctx: "AuthContext",
    *,
    owner_user_id: int,
    action: str,
    subject_type: str | None = None,
    subject_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    access = ctx.accessible_owners.get(owner_user_id)
    actor_role = "owner" if (access and access.is_self) else "staff"
    db.add(
        AuditLog(
            owner_user_id=owner_user_id,
            actor_user_id=ctx.user.id,
            actor_role=actor_role,
            action=action,
            subject_type=subject_type,
            subject_id=subject_id,
            payload=payload,
        )
    )
    await db.commit()
