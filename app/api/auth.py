"""Auth endpoints (/auth/*).

- POST /auth/tg — обмен Telegram initData на session token (HMAC-verify
  через `tg_auth.verify_init_data`). Single-token model: сессия не
  носит роли — права считаются per-endpoint через `accessible_owners` +
  `user.role`.
- GET  /auth/whoami — user + accessible_owners + available_roles +
  partner_status (для тех, у кого есть partner_profile).
- POST /auth/dev-login — bypass для локальной отладки, доступен только
  при `settings.dev_mode=True`; на проде 404 (см. Caddyfile `book.dev`).
"""
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_scope import load_accessible_owners
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AuthContext, current_user
from app.core.exceptions import APIError
from app.core.tg_auth import InitDataError, verify_init_data
from app.models.models import Lang, PartnerProfile, PartnerStaff, Session, User, UserRole
from app.schemas.auth import AuthTgRequest, AuthTgResponse, AuthTgUser
from app.schemas.partner import OwnerAccess, StaffPerms
from app.utils import get_or_create_client_for_user


async def _owners_response(db: AsyncSession, user: User) -> list[OwnerAccess]:
    """Build response list of accessible owners. Any user with a verified
    partner_profile or staff memberships gets entries — including admins who
    also happen to be partners (role can change after promote/demote)."""
    raw = await load_accessible_owners(db, user)
    return [
        OwnerAccess(
            owner_user_id=op.owner_user_id,
            owner_display_name=op.owner_display_name,
            is_self=op.is_self,
            perms=StaffPerms(
                manage_hotel=op.manage_hotel,
                manage_rooms=op.manage_rooms,
                manage_bookings=op.manage_bookings,
                manage_staff=op.manage_staff,
                chat_with_clients=op.chat_with_clients,
            ),
        )
        for op in raw.values()
    ]


def _partner_status(pp: PartnerProfile | None) -> str:
    if pp is None or pp.verified_at is None:
        return "pending"
    return "verified"


async def compute_available_roles(db: AsyncSession, user: User) -> list[UserRole]:
    """Roles a user is allowed to request:
      - client: always.
      - partner: has a PartnerProfile (any status) OR a partner_staff row.
      - admin: users.role == admin.
    """
    roles: list[UserRole] = [UserRole.client]

    pp_exists = (
        await db.execute(
            select(PartnerProfile.user_id).where(PartnerProfile.user_id == user.id)
        )
    ).scalar_one_or_none()
    staff_exists = None
    if pp_exists is None:
        staff_exists = (
            await db.execute(
                select(PartnerStaff.id).where(PartnerStaff.staff_user_id == user.id)
            )
        ).scalar_one_or_none()
    if pp_exists is not None or staff_exists is not None:
        roles.append(UserRole.partner)

    if user.role == UserRole.admin:
        roles.append(UserRole.admin)

    return roles


router = APIRouter(prefix="/auth", tags=["auth"])


_TG_LANG_MAP = {"ru": Lang.ru, "ky": Lang.ky, "en": Lang.en}


def _coerce_lang(language_code: str | None) -> Lang:
    if not language_code:
        return Lang.ru
    return _TG_LANG_MAP.get(language_code.split("-")[0].lower(), Lang.ru)


@router.post("/tg", response_model=AuthTgResponse)
async def auth_tg(payload: AuthTgRequest, db: AsyncSession = Depends(get_db)) -> AuthTgResponse:
    try:
        tg_user = verify_init_data(payload.init_data)
    except InitDataError as exc:
        raise APIError(401, "invalid_init_data", str(exc)) from exc

    telegram_id: int = tg_user["id"]
    first_name = tg_user.get("first_name")
    last_name = tg_user.get("last_name")
    username = tg_user.get("username")
    lang = _coerce_lang(tg_user.get("language_code"))

    existing = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = existing.scalar_one_or_none()
    is_new = False

    if user is None:
        user = User(
            telegram_id=telegram_id,
            role=UserRole.client,
            lang=lang,
            first_name=first_name,
            last_name=last_name,
            username=username,
        )
        db.add(user)
        await db.flush()
        is_new = True
    else:
        # Refresh display fields, do not overwrite primary role.
        if first_name and user.first_name != first_name:
            user.first_name = first_name
        if last_name and user.last_name != last_name:
            user.last_name = last_name
        if username and user.username != username:
            user.username = username

    # Auto-create a client profile for any TG user. Walk-in (no telegram_id)
    # rows live alongside in the same table; this one is the TG-linked profile.
    await get_or_create_client_for_user(db, user)

    pp = (
        await db.execute(select(PartnerProfile).where(PartnerProfile.user_id == user.id))
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(32)
    session = Session(
        token=token,
        user_id=user.id,
        expires_at=now + timedelta(seconds=settings.session_ttl_sec),
    )
    db.add(session)
    available_roles = await compute_available_roles(db, user)
    accessible_owners = await _owners_response(db, user)
    await db.commit()
    await db.refresh(user)

    return AuthTgResponse(
        token=token,
        expires_at=session.expires_at,
        user=AuthTgUser(
            id=user.id,
            telegram_id=user.telegram_id,
            role=user.role,
            lang=user.lang,
            first_name=user.first_name,
            is_new=is_new,
            partner_status=_partner_status(pp) if pp is not None else None,
            is_superadmin=user.is_superadmin,
        ),
        accessible_owners=accessible_owners,
        available_roles=available_roles,
    )


@router.get("/whoami")
async def whoami(
    ctx: AuthContext = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    pp = (
        await db.execute(
            select(PartnerProfile).where(PartnerProfile.user_id == ctx.user.id)
        )
    ).scalar_one_or_none()
    partner_status = _partner_status(pp) if pp is not None else None
    accessible_owners = await _owners_response(db, ctx.user)
    available_roles = await compute_available_roles(db, ctx.user)
    return {
        "user_id": ctx.user.id,
        "telegram_id": ctx.user.telegram_id,
        "role": ctx.user.role.value,
        "lang": ctx.user.lang.value,
        "first_name": ctx.user.first_name,
        "session_expires_at": ctx.session.expires_at.isoformat(),
        "partner_status": partner_status,
        "is_superadmin": ctx.user.is_superadmin,
        "bot_blocked_or_unreachable": ctx.user.bot_blocked_or_unreachable,
        "accessible_owners": [o.model_dump() for o in accessible_owners],
        "available_roles": [r.value for r in available_roles],
    }


@router.post("/dev-login", response_model=AuthTgResponse)
async def dev_login(
    telegram_id: int,
    first_name: str = "DevUser",
    db: AsyncSession = Depends(get_db),
) -> AuthTgResponse:
    """Bypass Telegram initData — only enabled when DEV_MODE=true.

    Use only in dev environment (vite dev-server, local browser without TG client).
    """
    if not settings.dev_mode:
        raise APIError(404, "not_found", "Dev login disabled")

    existing = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = existing.scalar_one_or_none()
    is_new = False
    if user is None:
        user = User(telegram_id=telegram_id, role=UserRole.client, lang=Lang.ru, first_name=first_name)
        db.add(user)
        await db.flush()
        is_new = True

    await get_or_create_client_for_user(db, user)

    pp = (
        await db.execute(select(PartnerProfile).where(PartnerProfile.user_id == user.id))
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(32)
    session = Session(
        token=token,
        user_id=user.id,
        expires_at=now + timedelta(seconds=settings.session_ttl_sec),
    )
    db.add(session)
    available_roles = await compute_available_roles(db, user)
    accessible_owners = await _owners_response(db, user)
    await db.commit()
    await db.refresh(user)
    return AuthTgResponse(
        token=token,
        expires_at=session.expires_at,
        user=AuthTgUser(
            id=user.id,
            telegram_id=user.telegram_id,
            role=user.role,
            lang=user.lang,
            first_name=user.first_name,
            is_new=is_new,
            partner_status=_partner_status(pp) if pp is not None else None,
            is_superadmin=user.is_superadmin,
        ),
        accessible_owners=accessible_owners,
        available_roles=available_roles,
    )
