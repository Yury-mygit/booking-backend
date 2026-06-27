"""Compute accessible_owners for a partner user (per-hotel scoped).

A user can access hotels of:
  - themselves, if they have a verified PartnerProfile (`is_self=True`,
    all perms true for all hotels)
  - any owner that listed them in `partner_staff` (perms from
    tri-state override + scoped roles)

Permission resolution (see `OwnerPerms.can`):
  1. `User.is_superadmin` → bypass.
  2. `is_self` (owner-of-partner) → bypass.
  3. Tri-state override on `PartnerStaff.perm_<name>` (global per partner;
     `None` falls through to roles).
  4. Scoped roles from `partner_staff_role`: a perm is granted if ANY
     active role with `hotel_id == <param>` OR `hotel_id IS NULL` (legacy
     global) has the flag set.

For coarse menu visibility (e.g. "should the 'rooms' tab be shown for this
owner at all"), use `any_hotel(perm)` — OR over all scoped+global roles.

A user with empty accessible_owners is rejected by `require_partner_or_staff`
(triggers `partner_pending` on FE).
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    PERM_NAMES,
    PartnerProfile,
    PartnerRole,
    PartnerStaff,
    PartnerStaffRole,
    User,
)


class OwnerPerms:
    __slots__ = (
        "owner_user_id",
        "owner_display_name",
        "is_self",
        "is_superadmin",
        "_override",
        "_scoped",
    )

    def __init__(
        self,
        owner_user_id: int,
        owner_display_name: str | None,
        is_self: bool,
        is_superadmin: bool,
        override: dict[str, bool | None] | None = None,
        scoped: dict[int | None, dict[str, bool]] | None = None,
    ) -> None:
        self.owner_user_id = owner_user_id
        self.owner_display_name = owner_display_name
        self.is_self = is_self
        self.is_superadmin = is_superadmin
        self._override = override or {p: None for p in PERM_NAMES}
        self._scoped = scoped or {}

    def can(self, hotel_id: int | None, perm: str) -> bool:
        """Check whether the user can perform `perm` on `hotel_id`.

        `hotel_id=None` means the action is not bound to a specific hotel
        (e.g. /p/staff CRUD); in that case only the tri-state override and
        NULL-scope (global) roles count.
        """
        if self.is_superadmin or self.is_self:
            return True
        ovr = self._override.get(perm)
        if ovr is not None:
            return ovr
        if self._scoped.get(hotel_id, {}).get(perm, False):
            return True
        if hotel_id is not None and self._scoped.get(None, {}).get(perm, False):
            return True
        return False

    def any_hotel(self, perm: str) -> bool:
        """Coarse aggregate: does the user have `perm` for any hotel of this
        owner? Used for menu/visibility checks (FE `/auth/me`)."""
        if self.is_superadmin or self.is_self:
            return True
        ovr = self._override.get(perm)
        if ovr is not None:
            return ovr
        return any(perms.get(perm, False) for perms in self._scoped.values())

    # Backward-compat accessors for legacy callers that read aggregated
    # perm-flags directly (e.g. /auth/me response builder). Equivalent to
    # `any_hotel(perm_name)`.
    @property
    def manage_hotel(self) -> bool:
        return self.any_hotel("manage_hotel")

    @property
    def manage_rooms(self) -> bool:
        return self.any_hotel("manage_rooms")

    @property
    def manage_bookings(self) -> bool:
        return self.any_hotel("manage_bookings")

    @property
    def manage_staff(self) -> bool:
        return self.any_hotel("manage_staff")

    @property
    def chat_with_clients(self) -> bool:
        return self.any_hotel("chat_with_clients")

    def has(self, perm: str) -> bool:
        """Legacy global check. Equivalent to `any_hotel(perm)`. Per-hotel
        callers should use `can(hotel_id, perm)`."""
        return self.any_hotel(perm)


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
            is_superadmin=bool(user.is_superadmin),
        )

    # Staff memberships. For each (owner_user_id, staff_row) load:
    #   - tri-state override (PartnerStaff.perm_*)
    #   - active scoped roles (PartnerStaffRole join PartnerRole), grouped
    #     by hotel_id.
    rows = (
        await db.execute(
            select(PartnerStaff, User, PartnerRole, PartnerStaffRole.hotel_id)
            .join(User, User.id == PartnerStaff.owner_user_id)
            .outerjoin(
                PartnerStaffRole,
                (PartnerStaffRole.staff_id == PartnerStaff.id)
                & (PartnerStaffRole.removed_at.is_(None)),
            )
            .outerjoin(PartnerRole, PartnerRole.id == PartnerStaffRole.role_id)
            .where(PartnerStaff.staff_user_id == user.id)
        )
    ).all()
    # agg: ps.id -> (ps, owner_User, list of (role, hotel_id))
    agg: dict[int, tuple[PartnerStaff, User, list[tuple[PartnerRole, int | None]]]] = {}
    for ps, owner, role, hotel_id in rows:
        if ps.id not in agg:
            agg[ps.id] = (ps, owner, [])
        if role is not None:
            agg[ps.id][2].append((role, hotel_id))

    for ps, owner, role_scopes in agg.values():
        if ps.owner_user_id == user.id:
            continue  # paranoia: never overshadow self entry

        override: dict[str, bool | None] = {
            p: getattr(ps, f"perm_{p}") for p in PERM_NAMES
        }
        scoped: dict[int | None, dict[str, bool]] = {}
        for role, hid in role_scopes:
            slot = scoped.setdefault(hid, {p: False for p in PERM_NAMES})
            for p in PERM_NAMES:
                if getattr(role, f"perm_{p}"):
                    slot[p] = True

        result[ps.owner_user_id] = OwnerPerms(
            owner_user_id=ps.owner_user_id,
            owner_display_name=owner.first_name,
            is_self=False,
            is_superadmin=bool(user.is_superadmin),
            override=override,
            scoped=scoped,
        )

    return result
