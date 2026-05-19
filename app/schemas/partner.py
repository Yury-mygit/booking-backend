from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.models import AvailabilityStatus, BookingStatus, DocKind, HotelStatus


class HotelCreate(BaseModel):
    name_ru: str = Field(min_length=1, max_length=256)
    name_ky: str | None = None
    name_en: str | None = None
    description_ru: str | None = None
    description_ky: str | None = None
    description_en: str | None = None
    city: str = Field(min_length=1, max_length=128)
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    photos: list[str] = Field(default_factory=list)


class HotelUpdate(BaseModel):
    name_ru: str | None = None
    name_ky: str | None = None
    name_en: str | None = None
    description_ru: str | None = None
    description_ky: str | None = None
    description_en: str | None = None
    city: str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    photos: list[str] | None = None
    status: HotelStatus | None = None


class ChecklistAction(BaseModel):
    tab: str | None = None
    nav: str | None = None  # "rooms" → /hotel/{id}/rooms
    room_id: int | None = None  # navigate to /room/{hotel_id}/{room_id}


class ChecklistItem(BaseModel):
    key: str  # i18n key, e.g. "status.check.hotel_photos"
    params: dict[str, int] = Field(default_factory=dict)
    kind: str  # "required" | "recommended"
    ok: bool
    action: ChecklistAction | None = None


class HotelStats(BaseModel):
    bookings_total: int
    bookings_active: int  # status in (pending, paid) and check_out >= today
    checkins_next_7d: int
    revenue_kgs_30d: int  # sum total_kgs for paid bookings created in last 30d
    last_booking_at: datetime | None


class HotelDashboard(BaseModel):
    can_publish: bool
    checks: list[ChecklistItem]
    stats: HotelStats


class HotelPartnerView(BaseModel):
    id: int
    slug: str
    name_ru: str
    name_ky: str | None
    name_en: str | None
    description_ru: str | None
    description_ky: str | None
    description_en: str | None
    city: str
    address: str | None
    lat: float | None
    lng: float | None
    photos: list[str]
    status: HotelStatus
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RoomCreate(BaseModel):
    name_ru: str = Field(min_length=1, max_length=256)
    name_ky: str | None = None
    name_en: str | None = None
    description_ru: str | None = None
    description_ky: str | None = None
    description_en: str | None = None
    capacity: int = Field(ge=1, le=20)
    price_kgs: int = Field(ge=0)
    floor: int | None = None
    beds: int | None = Field(default=None, ge=0)
    photos: list[str] = Field(default_factory=list)


class RoomUpdate(BaseModel):
    name_ru: str | None = None
    name_ky: str | None = None
    name_en: str | None = None
    description_ru: str | None = None
    description_ky: str | None = None
    description_en: str | None = None
    capacity: int | None = Field(default=None, ge=1, le=20)
    price_kgs: int | None = Field(default=None, ge=0)
    floor: int | None = None
    beds: int | None = Field(default=None, ge=0)
    photos: list[str] | None = None


class RoomPartnerView(BaseModel):
    id: int
    hotel_id: int
    name_ru: str
    name_ky: str | None
    name_en: str | None
    description_ru: str | None
    description_ky: str | None
    description_en: str | None
    capacity: int
    price_kgs: int
    floor: int | None
    beds: int | None
    photos: list[str]
    created_at: datetime


class AvailabilityRowIn(BaseModel):
    date: date
    status: AvailabilityStatus
    price_override: int | None = Field(default=None, ge=0)


class AvailabilityBatchUpdate(BaseModel):
    nights: list[AvailabilityRowIn] = Field(default_factory=list)


class AvailabilityRowOut(BaseModel):
    date: date
    status: AvailabilityStatus
    price_override: int | None


class ServiceCreate(BaseModel):
    name_ru: str = Field(min_length=1, max_length=256)
    name_ky: str | None = None
    name_en: str | None = None
    price_kgs: int | None = Field(default=None, ge=0)


class ServiceUpdate(BaseModel):
    name_ru: str | None = None
    name_ky: str | None = None
    name_en: str | None = None
    price_kgs: int | None = Field(default=None, ge=0)


class ServicePartnerView(BaseModel):
    id: int
    hotel_id: int
    name_ru: str
    name_ky: str | None
    name_en: str | None
    price_kgs: int | None
    created_at: datetime


class PartnerBookingView(BaseModel):
    id: int
    code: str
    room_id: int
    room_name_ru: str
    hotel_id: int
    hotel_name_ru: str
    client_first_name: str | None
    check_in: date
    check_out: date
    guests: int
    total_kgs: int
    status: BookingStatus
    postpay: bool
    confirmed: bool
    created_at: datetime


class PartnerBookingPostpaySet(BaseModel):
    postpay: bool


class WalkinBookingCreate(BaseModel):
    room_id: int
    check_in: date
    check_out: date
    guests: int = Field(default=1, ge=1, le=20)
    first_name: str = Field(min_length=1, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=256)
    doc_kind: DocKind | None = None
    doc_number: str | None = Field(default=None, max_length=64)


class ClientPartnerView(BaseModel):
    id: int
    user_id: int | None
    first_name: str
    last_name: str | None
    phone: str | None
    email: str | None
    doc_kind: DocKind | None
    doc_number: str | None
    photo_url: str | None
    bookings_count: int
    last_booking_date: date | None
    created_at: datetime


class ClientUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=256)
    doc_kind: DocKind | None = None
    doc_number: str | None = Field(default=None, max_length=64)


class ClientLookup(BaseModel):
    """Lookup by phone or email (digits-only / lowercase normalization on server)."""
    phone: str | None = None
    email: str | None = None


class RoomFlatView(BaseModel):
    room_id: int
    room_name_ru: str
    hotel_id: int
    hotel_name_ru: str
    capacity: int
    beds: int | None
    floor: int | None
    price_kgs: int
    today_status: AvailabilityStatus  # free / blocked / booked
    photo: str | None  # first photo of the room (or None)


# ─── Staff ──────────────────────────────────────────────────────────────

class StaffPerms(BaseModel):
    manage_hotel: bool = False
    manage_rooms: bool = False
    manage_bookings: bool = True
    manage_staff: bool = False


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


# ─── Staff invite (внешние ссылки) ──────────────────────────────────────

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


class StaffInviteAccept(BaseModel):
    token: str


# ─── Audit ──────────────────────────────────────────────────────────────

class AuditEntryView(BaseModel):
    id: int
    owner_user_id: int
    actor_user_id: int
    actor_display_name: str | None
    actor_role: str
    action: str
    subject_type: str | None
    subject_id: int | None
    payload: dict | None
    created_at: datetime
