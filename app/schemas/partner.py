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
    created_at: datetime


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
