"""Tag CRUD. Любой support-agent может создавать/править/удалять — это
организационная штука, не критичная privilege-разница."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext
from app.core.exceptions import APIError
from app.models.support import TicketTag
from app.schemas.support import TagCreateIn, TagOut, TagPatchIn
from app.services.support.permissions import require_support_agent

from . import _common as conv

router = APIRouter(tags=["admin-support"])


@router.get("/tags", response_model=list[TagOut])
async def list_tags(
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> list[TagOut]:
    rows = await db.execute(select(TicketTag).order_by(TicketTag.name))
    return [conv.tag_out(t) for t in rows.scalars().all()]


@router.post("/tags", response_model=TagOut, status_code=201)
async def create_tag(
    body: TagCreateIn,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> TagOut:
    existing = await db.execute(select(TicketTag).where(TicketTag.name == body.name))
    if existing.scalar_one_or_none() is not None:
        raise APIError(409, "tag_exists", f"Tag '{body.name}' already exists")

    tag = TicketTag(name=body.name, color=body.color, created_by_user_id=ctx.user.id)
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return conv.tag_out(tag)


@router.patch("/tags/{tag_id}", response_model=TagOut)
async def patch_tag(
    tag_id: int,
    body: TagPatchIn,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> TagOut:
    tag = await db.get(TicketTag, tag_id)
    if tag is None:
        raise APIError(404, "tag_not_found", "Tag not found")

    if body.name is not None and body.name != tag.name:
        dup = await db.execute(
            select(TicketTag).where(TicketTag.name == body.name, TicketTag.id != tag_id)
        )
        if dup.scalar_one_or_none() is not None:
            raise APIError(409, "tag_exists", f"Tag '{body.name}' already exists")
        tag.name = body.name
    if body.color is not None:
        tag.color = body.color

    await db.commit()
    await db.refresh(tag)
    return conv.tag_out(tag)


@router.delete("/tags/{tag_id}", status_code=204)
async def delete_tag(
    tag_id: int,
    ctx: AuthContext = Depends(require_support_agent),
    db: AsyncSession = Depends(get_db),
) -> None:
    tag = await db.get(TicketTag, tag_id)
    if tag is None:
        raise APIError(404, "tag_not_found", "Tag not found")
    # CASCADE на ticket_tag_assoc снимет связи; тикеты остаются.
    await db.delete(tag)
    await db.commit()
