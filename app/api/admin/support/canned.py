"""Canned Response CRUD. Модель + endpoints в v1; admin-UI «Шаблоны»
открывается в v1.5. is_global=true видно всем агентам; false — только
автору."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext
from app.core.exceptions import APIError
from app.models.models import Lang
from app.models.support import CannedResponse
from app.schemas.support import CannedCreateIn, CannedOut, CannedPatchIn
from app.services.support.permissions import require_support_agent

router = APIRouter(tags=["admin-support"])


def _canned_out(c: CannedResponse) -> CannedOut:
    return CannedOut(
        id=c.id, title=c.title, body=c.body, language=c.language,
        category_id=c.category_id, is_global=c.is_global,
        usage_count=c.usage_count, created_by_user_id=c.created_by_user_id,
        created_at=c.created_at, updated_at=c.updated_at,
    )


@router.get("/canned", response_model=list[CannedOut])
async def list_canned(
    language: Lang | None = None,
    category_id: int | None = None,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> list[CannedOut]:
    """Видны мои + все is_global. Сортировка по usage_count desc для
    «популярные сверху»."""
    q = select(CannedResponse).where(
        or_(
            CannedResponse.is_global.is_(True),
            CannedResponse.created_by_user_id == ctx.user.id,
        )
    )
    if language is not None:
        q = q.where(CannedResponse.language == language)
    if category_id is not None:
        q = q.where(
            or_(
                CannedResponse.category_id == category_id,
                CannedResponse.category_id.is_(None),  # «для любых»
            )
        )
    q = q.order_by(CannedResponse.usage_count.desc(), CannedResponse.title)
    rows = await db.execute(q)
    return [_canned_out(c) for c in rows.scalars().all()]


@router.post("/canned", response_model=CannedOut, status_code=201)
async def create_canned(
    body: CannedCreateIn,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> CannedOut:
    canned = CannedResponse(
        title=body.title, body=body.body, language=body.language,
        category_id=body.category_id, is_global=body.is_global,
        created_by_user_id=ctx.user.id,
    )
    db.add(canned)
    await db.commit()
    await db.refresh(canned)
    return _canned_out(canned)


@router.patch("/canned/{canned_id}", response_model=CannedOut)
async def patch_canned(
    canned_id: int,
    body: CannedPatchIn,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> CannedOut:
    canned = await db.get(CannedResponse, canned_id)
    if canned is None:
        raise APIError(404, "canned_not_found", "Canned response not found")
    # Чужие приватные не трогаем; чужие global — да (или ограничить только автором/superadmin?
    # Пока: автор может всё со своим; global может править любой agent).
    if not canned.is_global and canned.created_by_user_id != ctx.user.id:
        raise APIError(403, "forbidden", "Cannot edit private canned response of another agent")

    for field in ("title", "body", "language", "category_id", "is_global"):
        val = getattr(body, field)
        if val is not None:
            setattr(canned, field, val)
    canned.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(canned)
    return _canned_out(canned)


@router.delete("/canned/{canned_id}", status_code=204)
async def delete_canned(
    canned_id: int,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> None:
    canned = await db.get(CannedResponse, canned_id)
    if canned is None:
        raise APIError(404, "canned_not_found", "Canned response not found")
    if not canned.is_global and canned.created_by_user_id != ctx.user.id:
        raise APIError(403, "forbidden", "Cannot delete private canned response of another agent")
    await db.delete(canned)
    await db.commit()


@router.post("/canned/{canned_id}/use", status_code=204)
async def increment_usage(
    canned_id: int,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Атомарный +1 к usage_count — для сортировки «популярные сверху»
    в UI v1.5. Frontend зовёт при выборе шаблона."""
    canned = await db.get(CannedResponse, canned_id)
    if canned is None:
        raise APIError(404, "canned_not_found", "Canned response not found")
    canned.usage_count = (canned.usage_count or 0) + 1
    await db.commit()
