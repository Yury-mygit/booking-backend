from datetime import datetime

from pydantic import BaseModel, Field

from app.models.models import HotelService


class ServiceCreate(BaseModel):
    name_ru: str = Field(min_length=1, max_length=256)
    name_ky: str | None = None
    name_en: str | None = None
    price_kgs: int | None = Field(default=None, ge=0)


class ServiceUpdate(BaseModel):
    name_ru: str | None = None
    name_ky: str | None = None
    name_en: str | None = None
    price_kgs: int | None = Field(default=None, ge=0)


class ServicePartnerView(BaseModel):
    id: int
    hotel_id: int
    name_ru: str
    name_ky: str | None
    name_en: str | None
    price_kgs: int | None
    created_at: datetime

    @classmethod
    def from_model(cls, s: HotelService) -> "ServicePartnerView":
        return cls(
            id=s.id,
            hotel_id=s.hotel_id,
            name_ru=s.name_ru,
            name_ky=s.name_ky,
            name_en=s.name_en,
            price_kgs=s.price_kgs,
            created_at=s.created_at,
        )
