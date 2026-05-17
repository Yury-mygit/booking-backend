"""Compute accessible_owners for a partner user.

A user can access hotels of:
  - themselves, if they have a verified PartnerProfile (`is_self=True`, all perms true)
  - any owner that listed them in `partner_staff` (perms from row)

A user with empty accessible_owners is rejected by `require_partner_or_staff`
(triggers `partner_pending` on FE).
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import PartnerProfile, PartnerStaff, User


class OwnerPerms:
    __slots__ = (
        "owner_user_id",
        "owner_display_name",
        "is_self",
        "manage_hotel",
        "manage_rooms",
        "manage_bookings",
        "manage_staff",
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
    ) -> None:
        self.owner_user_id = owner_user_id
        self.owner_display_name = owner_display_name
        self.is_self = is_self
        self.manage_hotel = manage_hotel
        self.manage_rooms = manage_rooms
        self.manage_bookings = manage_bookings
        self.manage_staff = manage_staff

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
        )

    # Staff memberships.
    rows = (
        await db.execute(
            select(PartnerStaff, User)
            .join(User, User.id == PartnerStaff.owner_user_id)
            .where(PartnerStaff.staff_user_id == user.id)
        )
    ).all()
    for ps, owner in rows:
        if ps.owner_user_id == user.id:
            continue  # paranoia: never overshadow self entry with a stale row
        result[ps.owner_user_id] = OwnerPerms(
            owner_user_id=ps.owner_user_id,
            owner_display_name=owner.first_name,
            is_self=False,
            manage_hotel=ps.perm_manage_hotel,
            manage_rooms=ps.perm_manage_rooms,
            manage_bookings=ps.perm_manage_bookings,
            manage_staff=ps.perm_manage_staff,
        )

    return result
