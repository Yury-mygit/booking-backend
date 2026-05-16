from datetime import datetime, timezone

from fastapi import Depends, Header
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import APIError
from app.models.models import Session, User, UserRole


class AuthContext:
    def __init__(self, user: User, session: Session) -> None:
        self.user = user
        self.session = session
        self.role: UserRole = session.role


async def _resolve_session(
    authorization: str | None,
    db: AsyncSession,
) -> AuthContext:
    if not authorization:
        raise APIError(401, "unauthorized", "Missing Authorization header")
    if not authorization.startswith("Bearer "):
        raise APIError(401, "unauthorized", "Invalid authorization format")
    token = authorization[7:].strip()
    if not token:
        raise APIError(401, "unauthorized", "Empty token")

    row = await db.execute(
        select(Session, User).join(User, Session.user_id == User.id).where(Session.token == token)
    )
    pair = row.first()
    if pair is None:
        raise APIError(401, "unauthorized", "Unknown token")

    session, user = pair
    now = datetime.now(timezone.utc)
    if session.expires_at <= now:
        raise APIError(401, "token_expired", "Session expired")

    await db.execute(
        update(Session).where(Session.token == token).values(last_seen_at=now)
    )
    await db.commit()

    return AuthContext(user=user, session=session)


async def current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    return await _resolve_session(authorization, db)


def require_role(*allowed: UserRole):
    async def _dep(ctx: AuthContext = Depends(current_user)) -> AuthContext:
        if ctx.role not in allowed:
            raise APIError(403, "forbidden", f"Role {ctx.role.value} not allowed")
        return ctx

    return _dep
