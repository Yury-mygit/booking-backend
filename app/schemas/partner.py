from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.models import AvailabilityStatus, BookingStatus, HotelStatus


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
