"""SupportSettings GET / PATCH. Singleton (id=1). Только superadmin."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext, require_superadmin
from app.schemas.support import SupportSettingsOut, SupportSettingsPatchIn
from app.services.support import tickets as svc_tickets

router = APIRouter(tags=["admin-support"])


def _settings_out(s) -> SupportSettingsOut:
    return SupportSettingsOut(
        auto_close_days=s.auto_close_days,
        sla_response_low_h=s.sla_response_low_h,
        sla_response_normal_h=s.sla_response_normal_h,
        sla_response_high_h=s.sla_response_high_h,
        sla_response_urgent_h=s.sla_response_urgent_h,
        sla_resolution_low_h=s.sla_resolution_low_h,
        sla_resolution_normal_h=s.sla_resolution_normal_h,
        sla_resolution_high_h=s.sla_resolution_high_h,
        sla_resolution_urgent_h=s.sla_resolution_urgent_h,
        auto_greet_enabled=s.auto_greet_enabled,
        updated_at=s.updated_at,
    )


@router.get("/settings", response_model=SupportSettingsOut)
async def get_settings(
    ctx: AuthContext = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> SupportSettingsOut:
    s = await svc_tickets.get_settings(db)
    return _settings_out(s)


@router.patch("/settings", response_model=SupportSettingsOut)
async def patch_settings(
    body: SupportSettingsPatchIn,
    ctx: AuthContext = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> SupportSettingsOut:
    s = await svc_tickets.get_settings(db)
    for field, val in body.model_dump(exclude_unset=True).items():
        setattr(s, field, val)
    s.updated_at = datetime.now(timezone.utc)
    s.updated_by_user_id = ctx.user.id
    await db.commit()
    await db.refresh(s)
    return _settings_out(s)
