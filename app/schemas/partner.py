from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.models import (
    AvailabilityStatus,
    Booking,
    BookingStatus,
    Client,
    DocKind,
    Hotel,
    HotelService,
    HotelStatus,
    MealsKind,
    PartnerStaff,
    PartnerStaffInvite,
    Room,
    User,
)


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
    meals: MealsKind = MealsKind.none


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
    meals: MealsKind | None = None


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
    meals: MealsKind
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, h: Hotel) -> "HotelPartnerView":
        return cls(
            id=h.id,
            slug=h.slug,
            name_ru=h.name_ru,
            name_ky=h.name_ky,
            name_en=h.name_en,
            description_ru=h.description_ru,
            description_ky=h.description_ky,
            description_en=h.description_en,
            city=h.city,
            address=h.address,
            lat=float(h.lat) if h.lat is not None else None,
            lng=float(h.lng) if h.lng is not None else None,
            photos=h.photos or [],
            status=h.status,
            meals=h.meals,
            published_at=h.published_at,
            created_at=h.created_at,
            updated_at=h.updated_at,
        )


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
    single_beds: int = Field(default=0, ge=0)
    double_beds: int = Field(default=0, ge=0)
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
    single_beds: int | None = Field(default=None, ge=0)
    double_beds: int | None = Field(default=None, ge=0)
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
    single_beds: int
    double_beds: int
    photos: list[str]
    created_at: datetime

    @classmethod
    def from_model(cls, r: Room) -> "RoomPartnerView":
        return cls(
            id=r.id,
            hotel_id=r.hotel_id,
            name_ru=r.name_ru,
            name_ky=r.name_ky,
            name_en=r.name_en,
            description_ru=r.description_ru,
            description_ky=r.description_ky,
            description_en=r.description_en,
            capacity=r.capacity,
            price_kgs=r.price_kgs,
            floor=r.floor,
            single_beds=r.single_beds,
            double_beds=r.double_beds,
            photos=r.photos or [],
            created_at=r.created_at,
        )


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

    @classmethod
    def from_model(cls, s: HotelService) -> "ServicePartnerView":
        return cls(
            id=s.id,
            hotel_id=s.hotel_id,
            name_ru=s.name_ru,
            name_ky=s.name_ky,
            name_en=s.name_en,
            price_kgs=s.price_kgs,
            created_at=s.created_at,
        )


class PartnerBookingView(BaseModel):
    id: int
    code: str
    room_id: int
    room_name_ru: str
    hotel_id: int
    hotel_name_ru: str
    hotel_owner_user_id: int
    client_first_name: str | None
    check_in: date
    check_out: date
    guests: int
    total_kgs: int
    status: BookingStatus
    postpay: bool
    confirmed: bool
    created_at: datetime

    @classmethod
    def from_model(
        cls, b: Booking, r: Room, h: Hotel, c: Client
    ) -> "PartnerBookingView":
        return cls(
            id=b.id,
            code=b.code,
            room_id=r.id,
            room_name_ru=r.name_ru,
            hotel_id=h.id,
            hotel_name_ru=h.name_ru,
            hotel_owner_user_id=h.owner_user_id,
            client_first_name=c.first_name,
            check_in=b.check_in,
            check_out=b.check_out,
            guests=b.guests,
            total_kgs=b.total_kgs,
            status=b.status,
            postpay=b.postpay,
            confirmed=b.confirmed,
            created_at=b.created_at,
        )


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
    has_unread_chat: bool = False
    created_at: datetime

    @classmethod
    def from_model(
        cls,
        c: Client,
        *,
        bookings_count: int = 0,
        last_booking_date: date | None = None,
        has_unread_chat: bool = False,
    ) -> "ClientPartnerView":
        return cls(
            id=c.id,
            user_id=c.user_id,
            first_name=c.first_name,
            last_name=c.last_name,
            phone=c.phone,
            email=c.email,
            doc_kind=c.doc_kind,
            doc_number=c.doc_number,
            photo_url=c.photo_url,
            bookings_count=bookings_count,
            last_booking_date=last_booking_date,
            has_unread_chat=has_unread_chat,
            created_at=c.created_at,
        )


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
    single_beds: int
    double_beds: int
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
