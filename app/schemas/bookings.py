from datetime import date, datetime

from pydantic import BaseModel, field_serializer

from app.models.models import BookingStatus
from app.schemas._guests import GuestsFields
from app.services.photo_format import to_response_url, to_response_urls


class CreateBookingRequest(GuestsFields):
    room_id: int
    check_in: date
    check_out: date


class BookingMediaResponse(BaseModel):
    hotel_photos: list[str]
    room_photos: list[str]

    @field_serializer("hotel_photos", "room_photos")
    def _ser_photos(self, v: list[str]) -> list[str]:
        return to_response_urls(v)


class BookingResponse(BaseModel):
    id: int
    code: str
    room_id: int
    hotel_id: int
    hotel_name_ru: str
    hotel_photo: str | None
    check_in: date
    check_out: date
    adults: int
    children: int
    infants: int
    child_ages: list[int] | None
    total_kgs: int
    status: BookingStatus
    postpay: bool
    confirmed: bool
    created_at: datetime

    @field_serializer("hotel_photo")
    def _ser_hotel_photo(self, v: str | None) -> str | None:
        return to_response_url(v)
