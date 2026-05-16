import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AuthContext, current_user
from app.core.exceptions import APIError
from app.core.tg_auth import InitDataError, verify_init_data
from app.models.models import Lang, Session, User, UserRole
from app.schemas.auth import AuthTgRequest, AuthTgResponse, AuthTgUser

router = APIRouter(prefix="/auth", tags=["auth"])


_TG_LANG_MAP = {"ru": Lang.ru, "ky": Lang.ky, "en": Lang.en}


def _coerce_lang(language_code: str | None) -> Lang:
    if not language_code:
        return Lang.ru
    return _TG_LANG_MAP.get(language_code.split("-")[0].lower(), Lang.ru)


@router.post("/tg", response_model=AuthTgResponse)
async def auth_tg(payload: AuthTgRequest, db: AsyncSession = Depends(get_db)) -> AuthTgResponse:
    try:
        role, tg_user = verify_init_data(payload.init_data)
    except InitDataError as exc:
        raise APIError(401, "invalid_init_data", str(exc)) from exc

    telegram_id: int = tg_user["id"]
    first_name = tg_user.get("first_name")
    lang = _coerce_lang(tg_user.get("language_code"))

    existing = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = existing.scalar_one_or_none()
    is_new = False

    # Admin gate: opening the admin bot does NOT grant admin role on its own.
    # The user must be pre-assigned `users.role = admin` (via promote_to_admin
    # CLI or another admin's API call). Otherwise refuse — without this anyone
    # who knows the admin bot username could obtain an admin session.
    if role == UserRole.admin and (user is None or user.role != UserRole.admin):
        raise APIError(
            403,
            "forbidden",
            "Admin access requires pre-assigned admin role",
        )

    if user is None:
        user = User(
            telegram_id=telegram_id,
            role=role,
            lang=lang,
            first_name=first_name,
        )
        db.add(user)
        await db.flush()
        is_new = True
    else:
        # Refresh display fields, do not overwrite primary role.
        if first_name and user.first_name != first_name:
            user.first_name = first_name

    now = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(32)
    session = Session(
        token=token,
        user_id=user.id,
        role=role,
        expires_at=now + timedelta(seconds=settings.session_ttl_sec),
    )
    db.add(session)
    await db.commit()
    await db.refresh(user)

    return AuthTgResponse(
        token=token,
        expires_at=session.expires_at,
        user=AuthTgUser(
            id=user.id,
            telegram_id=user.telegram_id,
            role=role,
            lang=user.lang,
            first_name=user.first_name,
            is_new=is_new,
        ),
    )


@router.get("/whoami")
async def whoami(ctx: AuthContext = Depends(current_user)):
    return {
        "user_id": ctx.user.id,
        "telegram_id": ctx.user.telegram_id,
        "role": ctx.role.value,
        "lang": ctx.user.lang.value,
        "first_name": ctx.user.first_name,
        "session_expires_at": ctx.session.expires_at.isoformat(),
    }


@router.post("/dev-login", response_model=AuthTgResponse)
async def dev_login(
    telegram_id: int,
    first_name: str = "DevUser",
    role: UserRole = UserRole.client,
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
    if role == UserRole.admin and (user is None or user.role != UserRole.admin):
        raise APIError(403, "forbidden", "Admin access requires pre-assigned admin role")
    if user is None:
        user = User(telegram_id=telegram_id, role=role, lang=Lang.ru, first_name=first_name)
        db.add(user)
        await db.flush()
        is_new = True

    now = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(32)
    session = Session(
        token=token,
        user_id=user.id,
        role=role,
        expires_at=now + timedelta(seconds=settings.session_ttl_sec),
    )
    db.add(session)
    await db.commit()
    await db.refresh(user)
    return AuthTgResponse(
        token=token,
        expires_at=session.expires_at,
        user=AuthTgUser(
            id=user.id,
            telegram_id=user.telegram_id,
            role=role,
            lang=user.lang,
            first_name=user.first_name,
            is_new=is_new,
        ),
    )
