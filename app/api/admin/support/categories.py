"""Category CRUD. Создание/изменение/деактивация — superadmin only
(меняет визуальную структуру для всех юзеров). Удаление — только
если у категории нет тикетов; иначе deactivate (is_active=false)."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext, require_superadmin
from app.core.exceptions import APIError
from app.models.support import Ticket, TicketCategorySpec
from app.schemas.support import CategoryCreateIn, CategoryOutFull, CategoryPatchIn
from app.services.support.permissions import require_support_agent

from . import _common as conv

router = APIRouter(tags=["admin-support"])


@router.get("/categories", response_model=list[CategoryOutFull])
async def list_categories(
    ctx: AuthContext = Depends(require_support_agent),  # для list — любого агента
    db: AsyncSession = Depends(get_db),
) -> list[CategoryOutFull]:
    """Все категории (включая inactive) — для admin-UI."""
    rows = await db.execute(
        select(TicketCategorySpec).order_by(
            TicketCategorySpec.sort_order, TicketCategorySpec.id
        )
    )
    return [conv.category_out_full(c) for c in rows.scalars().all()]


@router.post("/categories", response_model=CategoryOutFull, status_code=201)
async def create_category(
    body: CategoryCreateIn,
    ctx: AuthContext = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> CategoryOutFull:
    existing = await db.execute(
        select(TicketCategorySpec).where(TicketCategorySpec.slug == body.slug)
    )
    if existing.scalar_one_or_none() is not None:
        raise APIError(409, "category_exists", f"Category '{body.slug}' already exists")

    cat = TicketCategorySpec(
        slug=body.slug, name_ru=body.name_ru, name_en=body.name_en, name_ky=body.name_ky,
        icon=body.icon, default_priority=body.default_priority,
        sort_order=body.sort_order,
    )
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return conv.category_out_full(cat)


@router.patch("/categories/{cat_id}", response_model=CategoryOutFull)
async def patch_category(
    cat_id: int,
    body: CategoryPatchIn,
    ctx: AuthContext = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> CategoryOutFull:
    cat = await db.get(TicketCategorySpec, cat_id)
    if cat is None:
        raise APIError(404, "category_not_found", "Category not found")

    for field in ("name_ru", "name_en", "name_ky", "icon", "default_priority",
                  "is_active", "sort_order"):
        val = getattr(body, field)
        if val is not None:
            setattr(cat, field, val)

    await db.commit()
    await db.refresh(cat)
    return conv.category_out_full(cat)


@router.delete("/categories/{cat_id}", status_code=204)
async def delete_category(
    cat_id: int,
    ctx: AuthContext = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> None:
    """DELETE только если категория пустая. Иначе ошибка — попроси
    deactivate (`PATCH is_active=false`)."""
    cat = await db.get(TicketCategorySpec, cat_id)
    if cat is None:
        raise APIError(404, "category_not_found", "Category not found")

    count = await db.execute(
        select(func.count()).select_from(Ticket).where(Ticket.category_id == cat_id)
    )
    if int(count.scalar() or 0) > 0:
        raise APIError(
            409, "category_in_use",
            "Category has tickets; deactivate via PATCH is_active=false instead",
        )

    await db.delete(cat)
    await db.commit()
