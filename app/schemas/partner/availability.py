from datetime import date

from pydantic import BaseModel, Field

from app.models.models import AvailabilityStatus


class AvailabilityRowIn(BaseModel):
    date: date
    status: AvailabilityStatus
    price_override: int | None = Field(default=None, ge=0)


class AvailabilityBatchUpdate(BaseModel):
    nights: list[AvailabilityRowIn] = Field(default_factory=list)


class AvailabilityRowOut(BaseModel):
    date: date
    status: AvailabilityStatus
    price_override: int | None
