from datetime import datetime, timezone

from fastapi import Depends, Header
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_scope import OwnerPerms, load_accessible_owners
from app.core.database import get_db
from app.core.exceptions import APIError
from app.models.models import Session, User, UserRole


class AuthContext:
    def __init__(
        self,
        user: User,
        session: Session,
        accessible_owners: dict[int, OwnerPerms] | None = None,
    ) -> None:
        self.user = user
        self.session = session
        self.role: UserRole = session.role
        self.accessible_owners: dict[int, OwnerPerms] = accessible_owners or {}


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
    """Variant B (single-app rework, 2026-05-24): authorization идёт по
    фактическим правам юзера, не по session.role (которое исторически
    выставлялось через `requested_role` в /auth/tg и теперь не reliable).

    Семантика по аргументам:
    - `UserRole.client`  — пропускаем любого залогиненного. Все TG-юзеры
       по дефолту client; «client endpoints» = «for any logged-in user».
    - `UserRole.admin`   — проверяем `user.role == admin` (БД-факт).
    - `UserRole.partner` — обычно не используется напрямую: для partner
       endpoints предпочтительнее `require_partner_or_staff`, которая
       проверяет ещё и `accessible_owners`.
    """
    allowed_set = set(allowed)

    async def _dep(ctx: AuthContext = Depends(current_user)) -> AuthContext:
        if allowed_set == {UserRole.client}:
            return ctx
        if UserRole.admin in allowed_set and ctx.user.role == UserRole.admin:
            return ctx
        if UserRole.partner in allowed_set:
            # Backward-compat fallthrough: для редких endpoints, которые
            # явно gate'или partner. Проверка по user, не session.role.
            if ctx.user.role == UserRole.partner or ctx.user.role == UserRole.admin:
                return ctx
        raise APIError(
            403,
            "forbidden",
            f"Role {ctx.user.role.value} not allowed (required: {','.join(r.value for r in allowed)})",
        )

    return _dep


async def require_partner_or_staff(
    ctx: AuthContext = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    """Variant B: пропускает любого залогиненного, у кого есть
    `accessible_owners` — собственный verified profile ИЛИ staff
    membership. Не проверяет `session.role` (single-token model).
    Admins тоже автоматически проходят, если они owners.
    """
    ctx.accessible_owners = await load_accessible_owners(db, ctx.user)
    if not ctx.accessible_owners:
        raise APIError(
            403,
            "partner_pending",
            "Partner access requires verified profile or staff membership",
        )
    return ctx


# Backwards-compat alias: existing endpoints reference the old name.
require_verified_partner = require_partner_or_staff
require_partner_access = require_partner_or_staff  # alias под карту single-app


async def require_admin_access(
    ctx: AuthContext = Depends(current_user),
) -> AuthContext:
    """Variant B: admin-only check по `user.role`, не session.role."""
    if ctx.user.role != UserRole.admin:
        raise APIError(403, "forbidden", "Admin access required")
    return ctx
