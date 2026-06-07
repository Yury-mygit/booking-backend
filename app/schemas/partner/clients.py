from datetime import date, datetime

from pydantic import BaseModel, Field, field_serializer

from app.models.models import Client, DocKind
from app.services.photo_format import to_response_url


class ClientPartnerView(BaseModel):
    id: int
    user_id: int | None
    first_name: str
    last_name: str | None
    phone: str | None
    email: str | None
    doc_kind: DocKind | None
    doc_number: str | None
    photo_url: str | None
    bookings_count: int
    last_booking_date: date | None
    has_unread_chat: bool = False
    created_at: datetime

    @field_serializer("photo_url")
    def _ser_photo_url(self, v: str | None) -> str | None:
        return to_response_url(v)

    @classmethod
    def from_model(
        cls,
        c: Client,
        *,
        bookings_count: int = 0,
        last_booking_date: date | None = None,
        has_unread_chat: bool = False,
    ) -> "ClientPartnerView":
        return cls(
            id=c.id,
            user_id=c.user_id,
            first_name=c.first_name,
            last_name=c.last_name,
            phone=c.phone,
            email=c.email,
            doc_kind=c.doc_kind,
            doc_number=c.doc_number,
            photo_url=c.photo_url,
            bookings_count=bookings_count,
            last_booking_date=last_booking_date,
            has_unread_chat=has_unread_chat,
            created_at=c.created_at,
        )


class ClientUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=256)
    doc_kind: DocKind | None = None
    doc_number: str | None = Field(default=None, max_length=64)


class ClientLookup(BaseModel):
    """Lookup by phone or email (digits-only / lowercase normalization on server)."""
    phone: str | None = None
    email: str | None = None
