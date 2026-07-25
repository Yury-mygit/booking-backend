from datetime import date, datetime

from pydantic import BaseModel

from app.models.models import (
    Booking,
    BookingStatus,
    Client,
    Hotel,
    Room,
)


class PartnerBookingView(BaseModel):
    id: int
    code: str
    room_id: int
    room_name_ru: str
    hotel_id: int
    hotel_name_ru: str
    hotel_owner_user_id: int
    client_first_name: str | None
    check_in: date
    check_out: date
    adults: int
    children: int
    infants: int
    child_ages: list[int] | None
    total_kgs: int
    status: BookingStatus
    confirmed: bool
    has_cancellation_request: bool = False
    created_at: datetime

    @classmethod
    def from_model(
        cls,
        b: Booking,
        r: Room,
        h: Hotel,
        c: Client,
        has_cancellation_request: bool = False,
    ) -> "PartnerBookingView":
        return cls(
            id=b.id,
            code=b.code,
            room_id=r.id,
            room_name_ru=r.name_ru,
            hotel_id=h.id,
            hotel_name_ru=h.name_ru,
            hotel_owner_user_id=h.owner_user_id,
            client_first_name=c.first_name,
            check_in=b.check_in,
            check_out=b.check_out,
            adults=b.adults,
            children=b.children,
            infants=b.infants,
            child_ages=b.child_ages,
            total_kgs=b.total_kgs,
            status=b.status,
            confirmed=b.confirmed,
            has_cancellation_request=has_cancellation_request,
            created_at=b.created_at,
        )
