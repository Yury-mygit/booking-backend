from datetime import date, time

from pydantic import BaseModel, Field, field_serializer, model_validator

from app.models.models import (
    HotelAmenity,
    MealsKind,
    RoomAmenity,
    ROOM_AMENITIES_PAID_ALLOWED,
)
from app.services.photo_format import to_response_urls


class RoomAmenityItem(BaseModel):
    kind: RoomAmenity
    paid: bool | None = None

    @model_validator(mode="after")
    def _paid_only_for_services(self) -> "RoomAmenityItem":
        if self.paid is not None and self.kind not in ROOM_AMENITIES_PAID_ALLOWED:
            raise ValueError(f"`paid` not allowed for amenity {self.kind.value}")
        return self


def serialize_hotel_amenities(items: list[HotelAmenity] | None) -> list[str]:
    """Convert HotelAmenity enums to plain str for JSONB storage."""
    if not items:
        return []
    return [a.value if isinstance(a, HotelAmenity) else a for a in items]


def serialize_room_amenities(items: list["RoomAmenityItem"] | None) -> list[dict]:
    """Convert RoomAmenityItem objects to plain dicts for JSONB storage.

    `paid` field is dropped when None to keep stored shape minimal.
    """
    if not items:
        return []
    out: list[dict] = []
    for it in items:
        kind = it.kind.value if isinstance(it.kind, RoomAmenity) else it.kind
        row: dict = {"kind": kind}
        if it.paid is not None:
            row["paid"] = bool(it.paid)
        out.append(row)
    return out


class HotelListItem(BaseModel):
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
    photos: list[str]
    meals: MealsKind
    min_price_kgs: int | None

    @field_serializer("photos")
    def _ser_photos(self, v: list[str]) -> list[str]:
        return to_response_urls(v)


class RoomCard(BaseModel):
    id: int
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
    available_for_dates: bool | None = None
    total_kgs_for_dates: int | None = None
    amenities: list[RoomAmenityItem] = Field(default_factory=list)

    @field_serializer("photos")
    def _ser_photos(self, v: list[str]) -> list[str]:
        return to_response_urls(v)


class ServicePublicView(BaseModel):
    id: int
    name_ru: str
    name_ky: str | None
    name_en: str | None
    price_kgs: int | None


class HotelDetails(BaseModel):
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
    meals: MealsKind
    amenities: list[HotelAmenity] = Field(default_factory=list)
    checkin_time: time | None = None
    checkout_time: time | None = None
    rooms: list[RoomCard]
    services: list[ServicePublicView]

    @field_serializer("photos")
    def _ser_photos(self, v: list[str]) -> list[str]:
        return to_response_urls(v)


class HotelSearchQuery(BaseModel):
    city: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    guests: int = Field(default=1, ge=1, le=20)
