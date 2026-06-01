from datetime import datetime

from pydantic import BaseModel, Field

from app.models.models import PartnerStaff, PartnerStaffInvite, User


class StaffPerms(BaseModel):
    manage_hotel: bool = False
    manage_rooms: bool = False
    manage_bookings: bool = True
    manage_staff: bool = False
    chat_with_clients: bool = False

    @classmethod
    def from_model(cls, obj: PartnerStaff | PartnerStaffInvite) -> "StaffPerms":
        """Принимает любую модель с perm_* атрибутами."""
        return cls(
            manage_hotel=obj.perm_manage_hotel,
            manage_rooms=obj.perm_manage_rooms,
            manage_bookings=obj.perm_manage_bookings,
            manage_staff=obj.perm_manage_staff,
            chat_with_clients=obj.perm_chat_with_clients,
        )


class OwnerAccess(BaseModel):
    owner_user_id: int
    owner_display_name: str | None
    is_self: bool
    perms: StaffPerms


class StaffCreate(BaseModel):
    telegram_id: int
    perms: StaffPerms = Field(default_factory=StaffPerms)
    note: str | None = Field(default=None, max_length=128)


class StaffUpdate(BaseModel):
    perms: StaffPerms | None = None
    note: str | None = Field(default=None, max_length=128)


class StaffView(BaseModel):
    id: int
    owner_user_id: int
    staff_user_id: int
    staff_telegram_id: int
    staff_display_name: str | None
    perms: StaffPerms
    note: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, ps: PartnerStaff, staff_user: User) -> "StaffView":
        return cls(
            id=ps.id,
            owner_user_id=ps.owner_user_id,
            staff_user_id=ps.staff_user_id,
            staff_telegram_id=staff_user.telegram_id,
            staff_display_name=staff_user.first_name,
            perms=StaffPerms.from_model(ps),
            note=ps.note,
            created_at=ps.created_at,
        )


class StaffInviteCreate(BaseModel):
    perms: StaffPerms = Field(default_factory=StaffPerms)
    note: str | None = Field(default=None, max_length=128)
    expires_in_days: int = Field(default=7, ge=1, le=90)


class StaffInviteView(BaseModel):
    id: int
    owner_user_id: int
    token: str
    url: str  # deep-link для отправки в чат
    perms: StaffPerms
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
            perms=StaffPerms.from_model(inv),
            note=inv.note,
            expires_at=inv.expires_at,
            used_at=inv.used_at,
            created_at=inv.created_at,
        )


class StaffInviteAccept(BaseModel):
    token: str
