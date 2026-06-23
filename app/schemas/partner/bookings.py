from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.models import (
    Booking,
    BookingStatus,
    Client,
    DocKind,
    Hotel,
    Room,
)
from app.schemas._guests import GuestsFields


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
    postpay: bool
    confirmed: bool
    created_at: datetime

    @classmethod
    def from_model(
        cls, b: Booking, r: Room, h: Hotel, c: Client
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
            postpay=b.postpay,
            confirmed=b.confirmed,
            created_at=b.created_at,
        )


class PartnerBookingPostpaySet(BaseModel):
    postpay: bool


class WalkinBookingCreate(GuestsFields):
    room_id: int
    check_in: date
    check_out: date
    first_name: str = Field(min_length=1, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=256)
    doc_kind: DocKind | None = None
    doc_number: str | None = Field(default=None, max_length=64)
