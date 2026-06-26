import enum
import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UserRole(str, enum.Enum):
    client = "client"
    partner = "partner"
    admin = "admin"


class Lang(str, enum.Enum):
    ru = "ru"
    ky = "ky"
    en = "en"


class HotelStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    blocked = "blocked"


class RoomStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    blocked = "blocked"


class MealsKind(str, enum.Enum):
    none = "none"
    breakfast = "breakfast"
    full_board = "full_board"


class HotelAmenity(str, enum.Enum):
    # general
    atm = "atm"
    reception_24h = "reception_24h"
    elevator = "elevator"
    press = "press"
    express_checkin = "express_checkin"
    # dining
    bar = "bar"
    free_tea_coffee = "free_tea_coffee"
    breakfast = "breakfast"
    restaurant = "restaurant"


class RoomAmenity(str, enum.Enum):
    # in-room
    air_conditioning = "air_conditioning"
    non_smoking = "non_smoking"
    room_service = "room_service"
    tv = "tv"
    bathrobe = "bathrobe"
    safe = "safe"
    toiletries = "toiletries"
    # services (paid flag allowed)
    ironing_supplies = "ironing_supplies"
    ironing_service = "ironing_service"
    shoe_cleaning = "shoe_cleaning"
    luggage_storage = "luggage_storage"
    phone = "phone"
    iron = "iron"


# Subset of RoomAmenity where `paid: bool` payload is meaningful. Used by
# Pydantic schemas to validate that hotel-included amenities never carry a
# `paid` flag.
ROOM_AMENITIES_PAID_ALLOWED = frozenset({
    RoomAmenity.ironing_supplies,
    RoomAmenity.ironing_service,
    RoomAmenity.shoe_cleaning,
    RoomAmenity.luggage_storage,
    RoomAmenity.phone,
    RoomAmenity.iron,
})


class AvailabilityStatus(str, enum.Enum):
    free = "free"
    blocked = "blocked"
    booked = "booked"


class BookingStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    cancelled = "cancelled"
    refunded = "refunded"


class PaymentProvider(str, enum.Enum):
    mock = "mock"
    elqr = "elqr"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    failed = "failed"
    refunded = "refunded"


class DocKind(str, enum.Enum):
    passport = "passport"
    id_card = "id_card"
    driving_license = "driving_license"
    other = "other"


class ChatSenderKind(str, enum.Enum):
    client = "client"
    hotel = "hotel"


class ChatSubjectType(str, enum.Enum):
    hotel = "hotel"
    booking = "booking"
    room = "room"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    role: Mapped[UserRole] = mapped_column(
        ENUM(UserRole, name="user_role"), nullable=False, server_default=UserRole.client.value
    )
    lang: Mapped[Lang] = mapped_column(
        ENUM(Lang, name="lang"), nullable=False, server_default=Lang.ru.value
    )
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(256))
    is_superadmin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    qr_image_url: Mapped[str | None] = mapped_column(String(512))
    bot_blocked_or_unreachable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PartnerProfile(Base):
    __tablename__ = "partner_profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    company_name: Mapped[str] = mapped_column(String(256), nullable=False)
    legal_inn: Mapped[str | None] = mapped_column(String(32))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), unique=True, index=True
    )
    first_name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(32), index=True)
    email: Mapped[str | None] = mapped_column(String(256), index=True)
    doc_kind: Mapped[DocKind | None] = mapped_column(ENUM(DocKind, name="doc_kind"))
    doc_number: Mapped[str | None] = mapped_column(String(64))
    photo_url: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Hotel(Base):
    __tablename__ = "hotels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    name_ru: Mapped[str] = mapped_column(String(256), nullable=False)
    name_ky: Mapped[str | None] = mapped_column(String(256))
    name_en: Mapped[str | None] = mapped_column(String(256))
    description_ru: Mapped[str | None] = mapped_column(Text)
    description_ky: Mapped[str | None] = mapped_column(Text)
    description_en: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    address: Mapped[str | None] = mapped_column(String(512))
    lat: Mapped[float | None] = mapped_column(Numeric(9, 6))
    lng: Mapped[float | None] = mapped_column(Numeric(9, 6))
    photos: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    meals: Mapped[MealsKind] = mapped_column(
        ENUM(MealsKind, name="meals_kind"),
        nullable=False,
        server_default=MealsKind.none.value,
    )
    amenities: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    checkin_time: Mapped[time | None] = mapped_column(Time(timezone=False))
    checkout_time: Mapped[time | None] = mapped_column(Time(timezone=False))
    status: Mapped[HotelStatus] = mapped_column(
        ENUM(HotelStatus, name="hotel_status"),
        nullable=False,
        server_default=HotelStatus.draft.value,
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name_ru: Mapped[str] = mapped_column(String(256), nullable=False)
    name_ky: Mapped[str | None] = mapped_column(String(256))
    name_en: Mapped[str | None] = mapped_column(String(256))
    description_ru: Mapped[str | None] = mapped_column(Text)
    description_ky: Mapped[str | None] = mapped_column(Text)
    description_en: Mapped[str | None] = mapped_column(Text)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    price_kgs: Mapped[int] = mapped_column(Integer, nullable=False)
    floor: Mapped[int | None] = mapped_column(Integer)
    single_beds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    double_beds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    photos: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    amenities: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    status: Mapped[RoomStatus] = mapped_column(
        ENUM(RoomStatus, name="room_status"),
        nullable=False,
        server_default=RoomStatus.published.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HotelService(Base):
    __tablename__ = "hotel_services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name_ru: Mapped[str] = mapped_column(String(256), nullable=False)
    name_ky: Mapped[str | None] = mapped_column(String(256))
    name_en: Mapped[str | None] = mapped_column(String(256))
    price_kgs: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Availability(Base):
    __tablename__ = "availability"

    room_id: Mapped[int] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    status: Mapped[AvailabilityStatus] = mapped_column(
        ENUM(AvailabilityStatus, name="availability_status"), nullable=False
    )
    price_override: Mapped[int | None] = mapped_column(Integer)


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True, nullable=False)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    room_id: Mapped[int] = mapped_column(
        ForeignKey("rooms.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    check_in: Mapped[date] = mapped_column(Date, nullable=False)
    check_out: Mapped[date] = mapped_column(Date, nullable=False)
    adults: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    children: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    infants: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    child_ages: Mapped[list[int] | None] = mapped_column(JSONB, nullable=True)
    total_kgs: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[BookingStatus] = mapped_column(
        ENUM(BookingStatus, name="booking_status"),
        nullable=False,
        server_default=BookingStatus.pending.value,
    )
    postpay: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Session(Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    provider: Mapped[PaymentProvider] = mapped_column(
        ENUM(PaymentProvider, name="payment_provider"), nullable=False
    )
    provider_ref: Mapped[str | None] = mapped_column(String(256), index=True)
    amount_kgs: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        ENUM(PaymentStatus, name="payment_status"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PartnerRole(Base):
    __tablename__ = "partner_role"
    __table_args__ = (UniqueConstraint("owner_user_id", "name", name="uq_partner_role_owner_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    perm_manage_hotel: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    perm_manage_rooms: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    perm_manage_bookings: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    perm_manage_staff: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    perm_chat_with_clients: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


PERM_NAMES = (
    "manage_hotel",
    "manage_rooms",
    "manage_bookings",
    "manage_staff",
    "chat_with_clients",
)


def compute_effective_perm(
    staff: "PartnerStaff",
    roles: "list[PartnerRole]",
    perm: str,
) -> bool:
    """Tri-state explicit override → OR-union over roles → False."""
    own = getattr(staff, f"perm_{perm}")
    if own is not None:
        return own
    return any(getattr(r, f"perm_{perm}") for r in roles)


class PartnerStaff(Base):
    __tablename__ = "partner_staff"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    staff_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    perm_manage_hotel: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    perm_manage_rooms: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    perm_manage_bookings: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    perm_manage_staff: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    perm_chat_with_clients: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    note: Mapped[str | None] = mapped_column(String(128))
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    middle_name: Mapped[str | None] = mapped_column(String(128))
    added_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PartnerStaffRole(Base):
    """Junction: many-to-many между сотрудником и ролями."""
    __tablename__ = "partner_staff_role"

    staff_id: Mapped[int] = mapped_column(
        ForeignKey("partner_staff.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("partner_role.id", ondelete="CASCADE"), primary_key=True
    )


class PartnerStaffInvite(Base):
    __tablename__ = "partner_staff_invite"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    actor_role: Mapped[str] = mapped_column(String(16), nullable=False)  # 'owner' | 'staff'
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_type: Mapped[str | None] = mapped_column(String(32))
    subject_id: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChatThread(Base):
    __tablename__ = "chat_threads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False
    )
    client_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client_last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hotel_last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_kind: Mapped[ChatSenderKind] = mapped_column(
        ENUM(ChatSenderKind, name="chat_sender_kind"), nullable=False
    )
    sender_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    subject_type: Mapped[ChatSubjectType | None] = mapped_column(
        ENUM(ChatSubjectType, name="chat_subject_type")
    )
    subject_id: Mapped[int | None] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
