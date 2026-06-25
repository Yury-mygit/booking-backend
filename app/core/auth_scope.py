"""Compute accessible_owners for a partner user.

A user can access hotels of:
  - themselves, if they have a verified PartnerProfile (`is_self=True`, all perms true)
  - any owner that listed them in `partner_staff` (perms from row)

A user with empty accessible_owners is rejected by `require_partner_or_staff`
(triggers `partner_pending` on FE).
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    PartnerProfile,
    PartnerRole,
    PartnerStaff,
    PartnerStaffRole,
    User,
    compute_effective_perm,
)


class OwnerPerms:
    __slots__ = (
        "owner_user_id",
        "owner_display_name",
        "is_self",
        "manage_hotel",
        "manage_rooms",
        "manage_bookings",
        "manage_staff",
        "chat_with_clients",
    )

    def __init__(
        self,
        owner_user_id: int,
        owner_display_name: str | None,
        is_self: bool,
        manage_hotel: bool,
        manage_rooms: bool,
        manage_bookings: bool,
        manage_staff: bool,
        chat_with_clients: bool,
    ) -> None:
        self.owner_user_id = owner_user_id
        self.owner_display_name = owner_display_name
        self.is_self = is_self
        self.manage_hotel = manage_hotel
        self.manage_rooms = manage_rooms
        self.manage_bookings = manage_bookings
        self.manage_staff = manage_staff
        self.chat_with_clients = chat_with_clients

    def has(self, perm: str) -> bool:
        return bool(getattr(self, perm, False))


async def load_accessible_owners(
    db: AsyncSession, user: User
) -> dict[int, OwnerPerms]:
    result: dict[int, OwnerPerms] = {}

    # Self — only if verified partner profile exists.
    pp = (
        await db.execute(
            select(PartnerProfile).where(PartnerProfile.user_id == user.id)
        )
    ).scalar_one_or_none()
    if pp is not None and pp.verified_at is not None:
        result[user.id] = OwnerPerms(
            owner_user_id=user.id,
            owner_display_name=user.first_name,
            is_self=True,
            manage_hotel=True,
            manage_rooms=True,
            manage_bookings=True,
            manage_staff=True,
            chat_with_clients=True,
        )

    # Staff memberships (M2M). Effective perms = explicit override on
    # PartnerStaff (NULL → OR(union ролей) → False). Outer-join по junction,
    # затем агрегируем роли на ps в Python (set может расширить ряды x N).
    rows = (
        await db.execute(
            select(PartnerStaff, User, PartnerRole)
            .join(User, User.id == PartnerStaff.owner_user_id)
            .outerjoin(PartnerStaffRole, PartnerStaffRole.staff_id == PartnerStaff.id)
            .outerjoin(PartnerRole, PartnerRole.id == PartnerStaffRole.role_id)
            .where(PartnerStaff.staff_user_id == user.id)
        )
    ).all()
    agg: dict[int, tuple[PartnerStaff, User, list[PartnerRole]]] = {}
    for ps, owner, role in rows:
        if ps.id not in agg:
            agg[ps.id] = (ps, owner, [])
        if role is not None:
            agg[ps.id][2].append(role)

    for ps, owner, roles in agg.values():
        if ps.owner_user_id == user.id:
            continue  # paranoia: never overshadow self entry with a stale row
        result[ps.owner_user_id] = OwnerPerms(
            owner_user_id=ps.owner_user_id,
            owner_display_name=owner.first_name,
            is_self=False,
            manage_hotel=compute_effective_perm(ps, roles, "manage_hotel"),
            manage_rooms=compute_effective_perm(ps, roles, "manage_rooms"),
            manage_bookings=compute_effective_perm(ps, roles, "manage_bookings"),
            manage_staff=compute_effective_perm(ps, roles, "manage_staff"),
            chat_with_clients=compute_effective_perm(ps, roles, "chat_with_clients"),
        )

    return result
