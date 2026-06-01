"""Admin /admin/users — list + verify-partner / promote-admin / revoke-partner / demote-admin.

Verify/revoke-partner — переключают `partner_profile.verified_at` и
`user.role` (partner ↔ client). Promote/demote-admin — меняют `user.role`
напрямую, без profile-связи. Revoke и demote также чистят активные
сессии затронутой роли, чтобы фронт не показывал устаревшую плашку
доступа после server-side даунгрейда.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext
from app.core.exceptions import APIError
from app.models.models import (
    Booking,
    Client,
    Hotel,
    PartnerProfile,
    Session,
    User,
    UserRole,
)
from app.schemas.admin import AdminUserView

from ._deps import admin_only

router = APIRouter()


@router.get("/users", response_model=list[AdminUserView])
async def list_users(
    role: UserRole | None = Query(default=None),
    verified: bool | None = Query(default=None),
    pending: bool | None = Query(default=None),
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    # Aggregate hotels/bookings per user in subqueries so the main row stays flat.
    hotels_cnt = (
        select(Hotel.owner_user_id.label("uid"), func.count(Hotel.id).label("cnt"))
        .group_by(Hotel.owner_user_id)
        .subquery()
    )
    bookings_cnt = (
        select(Client.user_id.label("uid"), func.count(Booking.id).label("cnt"))
        .join(Booking, Booking.client_id == Client.id)
        .where(Client.user_id.is_not(None))
        .group_by(Client.user_id)
        .subquery()
    )

    stmt = (
        select(
            User,
            PartnerProfile.user_id.label("pp_uid"),
            PartnerProfile.verified_at,
            hotels_cnt.c.cnt.label("hcnt"),
            bookings_cnt.c.cnt.label("bcnt"),
        )
        .outerjoin(PartnerProfile, PartnerProfile.user_id == User.id)
        .outerjoin(hotels_cnt, hotels_cnt.c.uid == User.id)
        .outerjoin(bookings_cnt, bookings_cnt.c.uid == User.id)
    )
    if role is not None:
        stmt = stmt.where(User.role == role)
    if verified is True:
        stmt = stmt.where(PartnerProfile.verified_at.is_not(None))
    elif verified is False:
        stmt = stmt.where(PartnerProfile.verified_at.is_(None))
    if pending is True:
        stmt = stmt.where(
            PartnerProfile.user_id.is_not(None), PartnerProfile.verified_at.is_(None)
        )
    stmt = stmt.order_by(User.created_at.desc()).limit(500)

    rows = (await db.execute(stmt)).all()
    return [
        AdminUserView.from_model(
            u,
            verified_at=verified_at,
            has_profile=pp_uid is not None,
            hotels_count=hcnt or 0,
            bookings_count=bcnt or 0,
        )
        for u, pp_uid, verified_at, hcnt, bcnt in rows
    ]


@router.post("/users/{user_id}/verify-partner", response_model=AdminUserView)
async def verify_partner(
    user_id: int,
    company_name: str = Query(...),
    legal_inn: str | None = Query(default=None),
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise APIError(404, "not_found", "User not found")
    if user.role != UserRole.partner:
        raise APIError(400, "bad_request", "User is not a partner")

    profile = (
        await db.execute(select(PartnerProfile).where(PartnerProfile.user_id == user_id))
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if profile is None:
        db.add(
            PartnerProfile(
                user_id=user_id,
                company_name=company_name,
                legal_inn=legal_inn,
                verified_at=now,
            )
        )
    else:
        profile.company_name = company_name
        profile.legal_inn = legal_inn
        profile.verified_at = now
    await db.commit()

    return AdminUserView.from_model(user, verified_at=now, has_profile=True,
                                hotels_count=0, bookings_count=0)


@router.post("/users/{user_id}/promote-admin", response_model=AdminUserView)
async def promote_admin(
    user_id: int,
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise APIError(404, "not_found", "User not found")
    user.role = UserRole.admin
    await db.commit()
    return AdminUserView.from_model(user, verified_at=None, has_profile=False,
                                hotels_count=0, bookings_count=0)


@router.post("/users/{user_id}/revoke-partner", response_model=AdminUserView)
async def revoke_partner(
    user_id: int,
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    """Drop partner_profile, reset role to client, kill partner sessions.
    Hotels owned by this user stay (FK survives) but require_verified_partner
    will start returning 403 — the user can reapply via the partner bot,
    which recreates the profile in pending state.

    Без сброса `users.role` и удаления partner-сессий фронт продолжает
    показывать партнёрскую плашку (по `role` из старой /auth/tg) при том
    что бэк уже режет доступ. По аналогии с demote_admin."""
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise APIError(404, "not_found", "User not found")
    profile = (
        await db.execute(select(PartnerProfile).where(PartnerProfile.user_id == user_id))
    ).scalar_one_or_none()
    if profile is None:
        raise APIError(409, "conflict", "User has no partner profile")
    await db.execute(delete(PartnerProfile).where(PartnerProfile.user_id == user_id))
    user.role = UserRole.client
    await db.execute(delete(Session).where(Session.user_id == user_id))
    await db.commit()
    await db.refresh(user)
    return AdminUserView.from_model(user, verified_at=None, has_profile=False,
                                hotels_count=0, bookings_count=0)


@router.post("/users/{user_id}/demote-admin", response_model=AdminUserView)
async def demote_admin(
    user_id: int,
    ctx: AuthContext = Depends(admin_only),
    db: AsyncSession = Depends(get_db),
):
    """Reverse promote-admin. Superadmins are immune (403). New role:
    partner if a partner_profile exists, otherwise client. Existing admin
    sessions for this user are deleted so the demotion takes effect now."""
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise APIError(404, "not_found", "User not found")
    if user.role != UserRole.admin:
        raise APIError(400, "bad_request", "User is not an admin")
    if user.is_superadmin:
        raise APIError(403, "forbidden", "Superadmin cannot be demoted")

    profile = (
        await db.execute(select(PartnerProfile).where(PartnerProfile.user_id == user_id))
    ).scalar_one_or_none()
    user.role = UserRole.partner if profile is not None else UserRole.client
    await db.execute(delete(Session).where(Session.user_id == user_id))
    await db.commit()
    await db.refresh(user)
    return AdminUserView.from_model(
        user,
        verified_at=profile.verified_at if profile is not None else None,
        has_profile=profile is not None,
        hotels_count=0,
        bookings_count=0,
    )
