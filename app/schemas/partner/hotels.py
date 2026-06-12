from datetime import datetime, time

from pydantic import BaseModel, Field, field_serializer

from app.models.models import Hotel, HotelAmenity, HotelStatus, MealsKind
from app.services.photo_format import to_response_urls


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
    amenities: list[HotelAmenity] = Field(default_factory=list)
    checkin_time: time | None = None
    checkout_time: time | None = None


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
    amenities: list[HotelAmenity] | None = None
    checkin_time: time | None = None
    checkout_time: time | None = None


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
    amenities: list[HotelAmenity] = Field(default_factory=list)
    checkin_time: time | None = None
    checkout_time: time | None = None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("photos")
    def _ser_photos(self, v: list[str]) -> list[str]:
        return to_response_urls(v)

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
            amenities=[HotelAmenity(a) for a in (h.amenities or [])],
            checkin_time=h.checkin_time,
            checkout_time=h.checkout_time,
            published_at=h.published_at,
            created_at=h.created_at,
            updated_at=h.updated_at,
        )
