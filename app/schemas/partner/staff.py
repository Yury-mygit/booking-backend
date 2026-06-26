from datetime import datetime

from pydantic import BaseModel, Field

from app.models.models import (
    PartnerRole,
    PartnerStaff,
    PartnerStaffInvite,  # noqa: F401  # re-exported via __init__
    User,
    compute_effective_perm,
)


class StaffPerms(BaseModel):
    manage_hotel: bool = False
    manage_rooms: bool = False
    manage_bookings: bool = True
    manage_staff: bool = False
    chat_with_clients: bool = False

    @classmethod
    def from_model(cls, obj: PartnerStaff | PartnerRole) -> "StaffPerms":
        """Принимает модель с perm_* атрибутами. NULL у PartnerStaff → False."""
        return cls(
            manage_hotel=bool(obj.perm_manage_hotel),
            manage_rooms=bool(obj.perm_manage_rooms),
            manage_bookings=bool(obj.perm_manage_bookings),
            manage_staff=bool(obj.perm_manage_staff),
            chat_with_clients=bool(obj.perm_chat_with_clients),
        )


class TriStatePerms(BaseModel):
    """Tri-state override: None = «наследовать из OR ролей», True/False = explicit."""

    manage_hotel: bool | None = None
    manage_rooms: bool | None = None
    manage_bookings: bool | None = None
    manage_staff: bool | None = None
    chat_with_clients: bool | None = None

    @classmethod
    def from_model(cls, obj: PartnerStaff) -> "TriStatePerms":
        return cls(
            manage_hotel=obj.perm_manage_hotel,
            manage_rooms=obj.perm_manage_rooms,
            manage_bookings=obj.perm_manage_bookings,
            manage_staff=obj.perm_manage_staff,
            chat_with_clients=obj.perm_chat_with_clients,
        )


def compute_effective(
    ps: PartnerStaff, roles: list[PartnerRole]
) -> StaffPerms:
    """Effective perms = explicit override на ps (если bool) | OR(roles) | False."""
    return StaffPerms(
        manage_hotel=compute_effective_perm(ps, roles, "manage_hotel"),
        manage_rooms=compute_effective_perm(ps, roles, "manage_rooms"),
        manage_bookings=compute_effective_perm(ps, roles, "manage_bookings"),
        manage_staff=compute_effective_perm(ps, roles, "manage_staff"),
        chat_with_clients=compute_effective_perm(ps, roles, "chat_with_clients"),
    )


class RoleView(BaseModel):
    id: int
    owner_user_id: int
    name: str
    perms: StaffPerms
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, role: PartnerRole) -> "RoleView":
        return cls(
            id=role.id,
            owner_user_id=role.owner_user_id,
            name=role.name,
            perms=StaffPerms.from_model(role),
            created_at=role.created_at,
            updated_at=role.updated_at,
        )


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    perms: StaffPerms = Field(default_factory=StaffPerms)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    perms: StaffPerms | None = None


class OwnerAccess(BaseModel):
    owner_user_id: int
    owner_display_name: str | None
    is_self: bool
    perms: StaffPerms


class StaffCreate(BaseModel):
    telegram_id: int
    role_ids: list[int] = Field(default_factory=list)
    perms: TriStatePerms = Field(default_factory=TriStatePerms)
    note: str | None = Field(default=None, max_length=128)
    first_name: str | None = Field(default=None, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    middle_name: str | None = Field(default=None, max_length=128)


class StaffUpdate(BaseModel):
    """role_ids: None = «не трогать junction»; [] = «снять все роли»; [1,2] = replace.
    perms: None = «не трогать матрицу»; объект = replace всех 5 tri-state полей."""

    role_ids: list[int] | None = None
    perms: TriStatePerms | None = None
    note: str | None = Field(default=None, max_length=128)


class StaffView(BaseModel):
    id: int
    owner_user_id: int
    staff_user_id: int
    staff_telegram_id: int
    staff_display_name: str | None
    first_name: str | None
    last_name: str | None
    middle_name: str | None
    roles: list[RoleView]
    perms: TriStatePerms
    effective_perms: StaffPerms
    note: str | None
    created_at: datetime

    @classmethod
    def from_model(
        cls, ps: PartnerStaff, staff_user: User, roles: list[PartnerRole] | None = None
    ) -> "StaffView":
        from app.core.display import staff_display_name
        roles = roles or []
        return cls(
            id=ps.id,
            owner_user_id=ps.owner_user_id,
            staff_user_id=ps.staff_user_id,
            staff_telegram_id=staff_user.telegram_id,
            staff_display_name=staff_display_name(ps, staff_user),
            first_name=ps.first_name,
            last_name=ps.last_name,
            middle_name=ps.middle_name,
            roles=[RoleView.from_model(r) for r in roles],
            perms=TriStatePerms.from_model(ps),
            effective_perms=compute_effective(ps, roles),
            note=ps.note,
            created_at=ps.created_at,
        )


class StaffInviteCreate(BaseModel):
    """Минимальный инвайт: новый сотрудник вступает без ролей и prefilled
    perms. Роли и override назначаются администратором после accept'а."""

    note: str | None = Field(default=None, max_length=128)
    expires_in_days: int = Field(default=7, ge=1, le=90)


class StaffInviteView(BaseModel):
    id: int
    owner_user_id: int
    token: str
    url: str  # deep-link для отправки в чат
    note: str | None
    expires_at: datetime
    used_at: datetime | None
    created_at: datetime

    @classmethod
    def from_model(cls, inv: PartnerStaffInvite) -> "StaffInviteView":
        from app.core.config import settings
        return cls(
            id=inv.id,
            owner_user_id=inv.owner_user_id,
            token=inv.token,
            url=f"https://t.me/{settings.tg_bot_username}?startapp=invite_{inv.token}",
            note=inv.note,
            expires_at=inv.expires_at,
            used_at=inv.used_at,
            created_at=inv.created_at,
        )


class StaffInviteAccept(BaseModel):
    token: str
