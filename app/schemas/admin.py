from datetime import date, datetime

from pydantic import BaseModel

from app.models.models import BookingStatus, HotelStatus, UserRole


class AdminUserView(BaseModel):
    id: int
    telegram_id: int
    role: UserRole
    first_name: str | None
    phone: str | None
    created_at: datetime
    is_verified_partner: bool


class AdminHotelView(BaseModel):
    id: int
    owner_user_id: int
    owner_first_name: str | None
    name_ru: str
    city: str
    status: HotelStatus
    created_at: datetime
    updated_at: datetime


class HotelStatusUpdate(BaseModel):
    status: HotelStatus


class AdminBookingView(BaseModel):
    id: int
    code: str
    user_id: int
    user_first_name: str | None
    room_id: int
    hotel_id: int
    hotel_name_ru: str
    check_in: date
    check_out: date
    guests: int
    total_kgs: int
    status: BookingStatus
    created_at: datetime


class MetricsView(BaseModel):
    users_total: int
    users_by_role: dict[str, int]
    verified_partners: int
    hotels_total: int
    hotels_by_status: dict[str, int]
    rooms_total: int
    bookings_total: int
    bookings_by_status: dict[str, int]
    revenue_kgs_paid: int
