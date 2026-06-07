from datetime import date

from pydantic import BaseModel, Field, field_serializer

from app.models.models import MealsKind
from app.services.photo_format import to_response_urls


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
