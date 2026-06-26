"""Partner roles (должности) — переиспользуемые наборы прав владельца.

Staff может ссылаться на одну роль (FK nullable); собственные `perm_*`
nullable работают как override (NULL = «наследовать», bool = explicit).
Эффективное право вычисляется `compute_effective_perm` в `models.py`.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import AuthContext, require_verified_partner
from app.core.exceptions import APIError
from app.models.models import PartnerRole, PartnerStaff, PartnerStaffRole, User
from app.schemas.partner.staff import (
    RoleCreate,
    RoleUpdate,
    RoleView,
    StaffPerms,
)

router = APIRouter()  # prefix задан в partner/__init__.py


def _require_owner_with_manage_staff(ctx: AuthContext, owner_id: int | None) -> int:
    if owner_id is None:
        self_access = ctx.accessible_owners.get(ctx.user.id)
        if self_access is None or not self_access.is_self:
            raise APIError(400, "bad_request", "owner_id is required")
        owner_id = ctx.user.id
    access = ctx.accessible_owners.get(owner_id)
    if access is None:
        raise APIError(404, "not_found", "Owner not accessible")
    if not access.has("manage_staff"):
        raise APIError(403, "permission_denied", "Missing permission: manage_staff")
    return owner_id


@router.get("/roles", response_model=list[RoleView])
async def list_roles(
    owner_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    owner_id = _require_owner_with_manage_staff(ctx, owner_id)
    rows = (
        await db.execute(
            select(PartnerRole)
            .where(PartnerRole.owner_user_id == owner_id)
            .order_by(PartnerRole.created_at.desc())
        )
    ).scalars().all()
    return [RoleView.from_model(r) for r in rows]


@router.post("/roles", response_model=RoleView, status_code=201)
async def create_role(
    payload: RoleCreate,
    owner_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    owner_id = _require_owner_with_manage_staff(ctx, owner_id)
    role = PartnerRole(
        owner_user_id=owner_id,
        name=payload.name.strip(),
        perm_manage_hotel=payload.perms.manage_hotel,
        perm_manage_rooms=payload.perms.manage_rooms,
        perm_manage_bookings=payload.perms.manage_bookings,
        perm_manage_staff=payload.perms.manage_staff,
        perm_chat_with_clients=payload.perms.chat_with_clients,
    )
    db.add(role)
    try:
        await db.commit()
    except Exception as e:
        # UniqueConstraint (owner_user_id, name) — 409
        await db.rollback()
        if "uq_partner_role_owner_name" in str(e):
            raise APIError(409, "name_taken", "Role with this name already exists")
        raise
    await db.refresh(role)
    await audit(
        db, ctx,
        owner_user_id=owner_id,
        action="role.create",
        subject_type="role",
        subject_id=role.id,
        payload={"name": role.name, "perms": payload.perms.model_dump()},
    )
    return RoleView.from_model(role)


@router.patch("/roles/{role_id}", response_model=RoleView)
async def update_role(
    role_id: int,
    payload: RoleUpdate,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    role = (
        await db.execute(select(PartnerRole).where(PartnerRole.id == role_id))
    ).scalar_one_or_none()
    if role is None:
        raise APIError(404, "not_found", "Role not found")
    _require_owner_with_manage_staff(ctx, role.owner_user_id)

    diff: dict = {}
    if payload.name is not None:
        new_name = payload.name.strip()
        if new_name != role.name:
            diff["name"] = {"before": role.name, "after": new_name}
            role.name = new_name
    if payload.perms is not None:
        before = StaffPerms.from_model(role).model_dump()
        role.perm_manage_hotel = payload.perms.manage_hotel
        role.perm_manage_rooms = payload.perms.manage_rooms
        role.perm_manage_bookings = payload.perms.manage_bookings
        role.perm_manage_staff = payload.perms.manage_staff
        role.perm_chat_with_clients = payload.perms.chat_with_clients
        after = payload.perms.model_dump()
        if before != after:
            diff["perms"] = {"before": before, "after": after}
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        if "uq_partner_role_owner_name" in str(e):
            raise APIError(409, "name_taken", "Role with this name already exists")
        raise
    await db.refresh(role)
    if diff:
        await audit(
            db, ctx,
            owner_user_id=role.owner_user_id,
            action="role.update",
            subject_type="role",
            subject_id=role.id,
            payload=diff,
        )
    return RoleView.from_model(role)


@router.delete("/roles/{role_id}", status_code=204)
async def delete_role(
    role_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    role = (
        await db.execute(select(PartnerRole).where(PartnerRole.id == role_id))
    ).scalar_one_or_none()
    if role is None:
        raise APIError(404, "not_found", "Role not found")
    _require_owner_with_manage_staff(ctx, role.owner_user_id)

    # 409 если есть junction-записи — owner должен переназначить
    in_use = (
        await db.execute(
            select(PartnerStaff, User)
            .join(PartnerStaffRole, PartnerStaffRole.staff_id == PartnerStaff.id)
            .join(User, User.id == PartnerStaff.staff_user_id)
            .where(PartnerStaffRole.role_id == role_id)
        )
    ).all()
    if in_use:
        from app.core.display import staff_display_name
        raise APIError(
            409,
            "role_in_use",
            "Role is assigned to staff members",
            detail={
                "staff": [
                    {
                        "id": ps.id,
                        "telegram_id": u.telegram_id,
                        "display_name": staff_display_name(ps, u),
                    }
                    for (ps, u) in in_use
                ]
            },
        )

    snapshot = {
        "name": role.name,
        "perms": StaffPerms.from_model(role).model_dump(),
    }
    await db.delete(role)
    await db.commit()
    await audit(
        db, ctx,
        owner_user_id=role.owner_user_id,
        action="role.delete",
        subject_type="role",
        subject_id=role_id,
        payload=snapshot,
    )
    return None
