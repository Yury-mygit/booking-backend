"""Partner staff: владелец → сотрудник по telegram_id.

Effective perm = explicit tri-state override на staff (если не NULL) |
OR-union прав по всем привязанным ролям | False. Roles = M2M через
`partner_staff_role`. Invite минимальный — только note + expires,
никаких prefilled ролей и perms (раздаются после accept'а).
"""
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext, current_user, require_verified_partner
from app.core.exceptions import APIError
from app.core.audit import audit
from app.services import scope
from app.models.models import (
    AuditLog,
    PartnerRole,
    PartnerStaff,
    PartnerStaffInvite,
    PartnerStaffRole,
    User,
    UserRole,
)
from app.schemas.partner import (
    StaffCreate,
    StaffInviteAccept,
    StaffInviteCreate,
    StaffInviteView,
    StaffUpdate,
    StaffView,
)
from app.schemas.partner.staff import TriStatePerms

router = APIRouter()  # prefix задан в partner/__init__.py


async def _load_roles(
    db: AsyncSession, owner_id: int, role_ids: list[int]
) -> list[PartnerRole]:
    """Загрузить роли и убедиться, что все принадлежат owner'у.

    Дубли в role_ids свёртываются. 404 если хоть один id не найден или
    принадлежит другому owner'у."""
    if not role_ids:
        return []
    uniq = list({rid for rid in role_ids})
    rows = (
        await db.execute(
            select(PartnerRole).where(PartnerRole.id.in_(uniq))
        )
    ).scalars().all()
    if len(rows) != len(uniq) or any(r.owner_user_id != owner_id for r in rows):
        raise APIError(404, "not_found", "Role not found")
    return rows


async def _load_staff_roles(
    db: AsyncSession, staff_id: int
) -> list[PartnerRole]:
    """Подгрузить все роли, привязанные к staff'у через junction."""
    return (
        await db.execute(
            select(PartnerRole)
            .join(PartnerStaffRole, PartnerStaffRole.role_id == PartnerRole.id)
            .where(PartnerStaffRole.staff_id == staff_id)
            .order_by(PartnerRole.name)
        )
    ).scalars().all()


async def _replace_staff_roles(
    db: AsyncSession, staff_id: int, role_ids: list[int]
) -> None:
    """Полностью заменить junction-rows у staff'а. role_ids уже валидирован."""
    await db.execute(
        delete(PartnerStaffRole).where(PartnerStaffRole.staff_id == staff_id)
    )
    for rid in {rid for rid in role_ids}:
        db.add(PartnerStaffRole(staff_id=staff_id, role_id=rid))


# ─── Staff ────────────────────────────────────────────────────────────────

@router.get("/staff", response_model=list[StaffView])
async def list_staff(
    owner_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    # Default: list staff for the owner-self (if any) — staff can request
    # explicitly by owner_id (must have manage_staff there).
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

    staff_rows = (
        await db.execute(
            select(PartnerStaff, User)
            .join(User, User.id == PartnerStaff.staff_user_id)
            .where(PartnerStaff.owner_user_id == owner_id)
            .order_by(PartnerStaff.created_at.desc())
        )
    ).all()
    if not staff_rows:
        return []
    # Один SQL: junction × role для всех staff'ов сразу. Группируем в Python.
    staff_ids = [ps.id for (ps, _) in staff_rows]
    role_rows = (
        await db.execute(
            select(PartnerStaffRole.staff_id, PartnerRole)
            .join(PartnerRole, PartnerRole.id == PartnerStaffRole.role_id)
            .where(PartnerStaffRole.staff_id.in_(staff_ids))
            .order_by(PartnerRole.name)
        )
    ).all()
    roles_by_staff: dict[int, list[PartnerRole]] = {}
    for sid, role in role_rows:
        roles_by_staff.setdefault(sid, []).append(role)
    return [
        StaffView.from_model(ps, u, roles_by_staff.get(ps.id, []))
        for (ps, u) in staff_rows
    ]


@router.post("/staff", response_model=StaffView, status_code=201)
async def add_staff(
    payload: StaffCreate,
    owner_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
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

    if payload.telegram_id == 0:
        raise APIError(400, "bad_request", "Invalid telegram_id")

    # Look up or create the user stub.
    staff_user = (
        await db.execute(select(User).where(User.telegram_id == payload.telegram_id))
    ).scalar_one_or_none()
    if staff_user is None:
        staff_user = User(
            telegram_id=payload.telegram_id,
            role=UserRole.partner,
        )
        db.add(staff_user)
        await db.flush()
    elif staff_user.role == UserRole.admin:
        raise APIError(409, "incompatible_role", "Cannot add admin as staff")
    elif staff_user.role == UserRole.client:
        staff_user.role = UserRole.partner  # upgrade

    if staff_user.id == owner_id:
        raise APIError(400, "bad_request", "Cannot add yourself as your own staff")

    existing = (
        await db.execute(
            select(PartnerStaff).where(
                PartnerStaff.owner_user_id == owner_id,
                PartnerStaff.staff_user_id == staff_user.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise APIError(409, "already_member", "User is already a staff member")

    roles = await _load_roles(db, owner_id, payload.role_ids)

    ps = PartnerStaff(
        owner_user_id=owner_id,
        staff_user_id=staff_user.id,
        perm_manage_hotel=payload.perms.manage_hotel,
        perm_manage_rooms=payload.perms.manage_rooms,
        perm_manage_bookings=payload.perms.manage_bookings,
        perm_manage_staff=payload.perms.manage_staff,
        perm_chat_with_clients=payload.perms.chat_with_clients,
        note=payload.note,
        added_by_user_id=ctx.user.id,
    )
    db.add(ps)
    await db.flush()  # нужен ps.id для junction
    await _replace_staff_roles(db, ps.id, [r.id for r in roles])
    await db.commit()
    await db.refresh(ps)
    await audit(
        db, ctx,
        owner_user_id=owner_id,
        action="staff.add",
        subject_type="staff",
        subject_id=ps.id,
        payload={
            "staff_user_id": staff_user.id,
            "telegram_id": staff_user.telegram_id,
            "role_ids": [r.id for r in roles],
            "perms": payload.perms.model_dump(),
            "note": payload.note,
        },
    )
    return StaffView.from_model(ps, staff_user, roles)


@router.put("/staff/{staff_id}", response_model=StaffView)
async def update_staff(
    staff_id: int,
    payload: StaffUpdate,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            select(PartnerStaff, User)
            .join(User, User.id == PartnerStaff.staff_user_id)
            .where(PartnerStaff.id == staff_id)
        )
    ).first()
    if row is None:
        raise APIError(404, "not_found", "Staff member not found")
    ps, staff_user = row
    access = ctx.accessible_owners.get(ps.owner_user_id)
    if access is None:
        raise APIError(404, "not_found", "Staff member not found")
    if not access.has("manage_staff"):
        raise APIError(403, "permission_denied", "Missing permission: manage_staff")

    diff: dict = {}
    if payload.role_ids is not None:
        # Полная замена набора ролей (валидируем принадлежность owner'у).
        new_roles = await _load_roles(db, ps.owner_user_id, payload.role_ids)
        current = await _load_staff_roles(db, ps.id)
        before_ids = sorted(r.id for r in current)
        after_ids = sorted({r.id for r in new_roles})
        if before_ids != after_ids:
            diff["role_ids"] = {"before": before_ids, "after": after_ids}
            await _replace_staff_roles(db, ps.id, after_ids)
    if payload.perms is not None:
        before = TriStatePerms.from_model(ps).model_dump()
        ps.perm_manage_hotel = payload.perms.manage_hotel
        ps.perm_manage_rooms = payload.perms.manage_rooms
        ps.perm_manage_bookings = payload.perms.manage_bookings
        ps.perm_manage_staff = payload.perms.manage_staff
        ps.perm_chat_with_clients = payload.perms.chat_with_clients
        after = payload.perms.model_dump()
        if before != after:
            diff["perms"] = {"before": before, "after": after}
    if payload.note is not None and payload.note != ps.note:
        diff["note"] = {"before": ps.note, "after": payload.note}
        ps.note = payload.note
    await db.commit()
    await db.refresh(ps)
    roles = await _load_staff_roles(db, ps.id)
    if diff:
        await audit(
            db, ctx,
            owner_user_id=ps.owner_user_id,
            action="staff.update",
            subject_type="staff",
            subject_id=ps.id,
            payload=diff,
        )
    return StaffView.from_model(ps, staff_user, roles)


@router.delete("/staff/{staff_id}", status_code=204)
async def remove_staff(
    staff_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    ps = (
        await db.execute(select(PartnerStaff).where(PartnerStaff.id == staff_id))
    ).scalar_one_or_none()
    if ps is None:
        raise APIError(404, "not_found", "Staff member not found")
    access = ctx.accessible_owners.get(ps.owner_user_id)
    if access is None:
        raise APIError(404, "not_found", "Staff member not found")
    if not access.has("manage_staff"):
        raise APIError(403, "permission_denied", "Missing permission: manage_staff")

    owner_id = ps.owner_user_id
    role_ids_snapshot = [r.id for r in await _load_staff_roles(db, ps.id)]
    snapshot = {
        "staff_user_id": ps.staff_user_id,
        "role_ids": role_ids_snapshot,
        "perms": TriStatePerms.from_model(ps).model_dump(),
        "note": ps.note,
    }
    # junction уйдёт CASCADE'ом по FK
    await db.delete(ps)
    await db.commit()
    await audit(
        db, ctx,
        owner_user_id=owner_id,
        action="staff.remove",
        subject_type="staff",
        subject_id=staff_id,
        payload=snapshot,
    )
    return None


# ─── Staff invite (внешние ссылки) ────────────────────────────────────────

@router.post("/staff/invites", response_model=StaffInviteView, status_code=201)
async def create_staff_invite(
    payload: StaffInviteCreate,
    owner_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
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

    token = secrets.token_hex(24)
    expires_at = datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days)
    inv = PartnerStaffInvite(
        token=token,
        owner_user_id=owner_id,
        created_by_user_id=ctx.user.id,
        note=payload.note,
        expires_at=expires_at,
    )
    db.add(inv)
    await db.commit()
    await db.refresh(inv)
    await audit(
        db, ctx,
        owner_user_id=owner_id,
        action="staff.invite_create",
        subject_type="staff_invite",
        subject_id=inv.id,
        payload={
            "expires_at": inv.expires_at.isoformat(),
            "note": payload.note,
        },
    )
    return StaffInviteView.from_model(inv)


@router.get("/staff/invites", response_model=list[StaffInviteView])
async def list_staff_invites(
    owner_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    accessible_ids = scope.scope_owner_ids(ctx, owner_id)
    # Только владельцы, где у текущего user есть manage_staff.
    allowed = [
        oid for oid in accessible_ids
        if ctx.accessible_owners[oid].has("manage_staff")
    ]
    if not allowed:
        return []
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(PartnerStaffInvite)
            .where(PartnerStaffInvite.owner_user_id.in_(allowed))
            .where(PartnerStaffInvite.used_at.is_(None))
            .where(PartnerStaffInvite.expires_at > now)
            .order_by(PartnerStaffInvite.created_at.desc())
        )
    ).scalars().all()
    return [StaffInviteView.from_model(inv) for inv in rows]


@router.delete("/staff/invites/{invite_id}", status_code=204)
async def revoke_staff_invite(
    invite_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    inv = (
        await db.execute(
            select(PartnerStaffInvite).where(PartnerStaffInvite.id == invite_id)
        )
    ).scalar_one_or_none()
    if inv is None:
        raise APIError(404, "not_found", "Invite not found")
    access = ctx.accessible_owners.get(inv.owner_user_id)
    if access is None or not access.has("manage_staff"):
        raise APIError(403, "permission_denied", "Missing permission: manage_staff")
    if inv.used_at is not None:
        # already revoked or used — idempotent
        return None
    inv.used_at = datetime.now(timezone.utc)  # marks as inactive without consumer
    await db.commit()
    await audit(
        db, ctx,
        owner_user_id=inv.owner_user_id,
        action="staff.invite_revoke",
        subject_type="staff_invite",
        subject_id=inv.id,
        payload={"token_prefix": inv.token[:8]},
    )
    return None


@router.post("/staff/invite/accept", response_model=StaffView, status_code=201)
async def accept_staff_invite(
    payload: StaffInviteAccept,
    ctx: AuthContext = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Любой авторизованный пользователь принимает invite. После accept
    становится partner-staff заданного owner с perms из invite."""
    inv = (
        await db.execute(
            select(PartnerStaffInvite).where(PartnerStaffInvite.token == payload.token)
        )
    ).scalar_one_or_none()
    if inv is None:
        raise APIError(404, "not_found", "Invite not found")
    now = datetime.now(timezone.utc)
    if inv.used_at is not None:
        raise APIError(410, "invite_used", "Invite already used or revoked")
    if inv.expires_at <= now:
        raise APIError(410, "invite_expired", "Invite has expired")
    if ctx.user.id == inv.owner_user_id:
        raise APIError(400, "bad_request", "Cannot accept own invite")
    if ctx.user.role == UserRole.admin:
        raise APIError(409, "incompatible_role", "Cannot add admin as staff")

    existing = (
        await db.execute(
            select(PartnerStaff).where(
                PartnerStaff.owner_user_id == inv.owner_user_id,
                PartnerStaff.staff_user_id == ctx.user.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise APIError(409, "already_member", "Already a staff member of this owner")

    if ctx.user.role == UserRole.client:
        ctx.user.role = UserRole.partner

    # Invite минимальный — staff создаётся без ролей и perms.
    # Админ потом раздаёт через PUT /p/staff/{id}.
    ps = PartnerStaff(
        owner_user_id=inv.owner_user_id,
        staff_user_id=ctx.user.id,
        note=inv.note,
        added_by_user_id=inv.created_by_user_id,
    )
    db.add(ps)
    inv.used_at = now
    inv.used_by_user_id = ctx.user.id
    await db.commit()
    await db.refresh(ps)
    # Audit под owner'ом, актор = принявший себя staff (self-onboarding).
    db.add(AuditLog(
        owner_user_id=inv.owner_user_id,
        actor_user_id=ctx.user.id,
        actor_role="staff",
        action="staff.invite_accept",
        subject_type="staff",
        subject_id=ps.id,
        payload={"invite_id": inv.id},
    ))
    await db.commit()
    return StaffView.from_model(ps, ctx.user, [])


