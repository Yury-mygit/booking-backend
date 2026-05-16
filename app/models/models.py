import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
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
    phone: Mapped[str | None] = mapped_column(String(32))
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


class Hotel(Base):
    __tablename__ = "hotels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
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
    status: Mapped[HotelStatus] = mapped_column(
        ENUM(HotelStatus, name="hotel_status"),
        nullable=False,
        server_default=HotelStatus.draft.value,
    )
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
    photos: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
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
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    room_id: Mapped[int] = mapped_column(
        ForeignKey("rooms.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    check_in: Mapped[date] = mapped_column(Date, nullable=False)
    check_out: Mapped[date] = mapped_column(Date, nullable=False)
    guests: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    total_kgs: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[BookingStatus] = mapped_column(
        ENUM(BookingStatus, name="booking_status"),
        nullable=False,
        server_default=BookingStatus.pending.value,
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
    role: Mapped[UserRole] = mapped_column(
        ENUM(UserRole, name="user_role", create_type=False), nullable=False
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
