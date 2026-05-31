from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.models import BookingStatus


class CreateBookingRequest(BaseModel):
    room_id: int
    check_in: date
    check_out: date
    guests: int = Field(default=1, ge=1, le=20)


class BookingResponse(BaseModel):
    id: int
    code: str
    room_id: int
    hotel_id: int
    hotel_name_ru: str
    hotel_photo: str | None
    check_in: date
    check_out: date
    guests: int
    total_kgs: int
    status: BookingStatus
    postpay: bool
    confirmed: bool
    created_at: datetime
