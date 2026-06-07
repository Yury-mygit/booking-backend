from datetime import datetime

from pydantic import BaseModel, Field, field_serializer

from app.models.models import AvailabilityStatus, Room
from app.services.photo_format import to_response_url, to_response_urls


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

    @field_serializer("photos")
    def _ser_photos(self, v: list[str]) -> list[str]:
        return to_response_urls(v)

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

    @field_serializer("photo")
    def _ser_photo(self, v: str | None) -> str | None:
        return to_response_url(v)
