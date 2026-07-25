import enum
from datetime import date, datetime

from pydantic import BaseModel, Field, field_serializer, field_validator

from app.models.models import BookingStatus
from app.schemas._guests import GuestsFields
from app.services.photo_format import to_response_url, to_response_urls


class CancellationReasonCode(str, enum.Enum):
    plans_changed = "plans_changed"
    found_better = "found_better"
    booking_error = "booking_error"
    partner_issue = "partner_issue"
    other = "other"


class CancellationRequestBody(BaseModel):
    reasons: list[CancellationReasonCode] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("note")
    @classmethod
    def _strip_note(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("reasons")
    @classmethod
    def _uniq_reasons(cls, v: list[CancellationReasonCode]) -> list[CancellationReasonCode]:
        # Порядок сохраняем как прислал клиент, дубликаты убираем.
        seen: set[CancellationReasonCode] = set()
        out: list[CancellationReasonCode] = []
        for r in v:
            if r not in seen:
                seen.add(r)
                out.append(r)
        return out


class CancellationRequestResponse(BaseModel):
    booking_code: str
    requested_at: datetime


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
    confirmed: bool
    created_at: datetime

    @field_serializer("hotel_photo")
    def _ser_hotel_photo(self, v: str | None) -> str | None:
        return to_response_url(v)
