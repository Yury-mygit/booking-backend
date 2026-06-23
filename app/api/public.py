"""Public catalog (/public/*, без auth).

GET /public/hotels — список published-отелей (фильтры city, check_in/out).
GET /public/hotels/{slug_or_id} — детальная карточка + комнаты + услуги.

Возвращаются только `HotelStatus.published` — pending/draft/archived
невидимы. Если запрошен диапазон дат — комнаты фильтруются по
`Availability` (отсутствие записи = свободно; см. `_validate_date_range`).

SSE на `/public/hotels/{slug}/events` живёт в `events.py`.
"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, exists, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import APIError
from app.models.models import (
    Availability,
    AvailabilityStatus,
    Hotel,
    HotelService,
    HotelStatus,
    Room,
    RoomStatus,
)
from app.schemas.hotels import HotelDetails, HotelListItem, RoomCard, ServicePublicView
from app.utils import date_range_nights

router = APIRouter(prefix="/public", tags=["public"])


def _validate_date_range(check_in: date | None, check_out: date | None) -> None:
    if (check_in is None) ^ (check_out is None):
        raise APIError(400, "bad_request", "Pass both check_in and check_out or neither")
    if check_in and check_out and check_out <= check_in:
        raise APIError(400, "bad_request", "check_out must be after check_in")


def _room_unavailable_clause(check_in: date, check_out: date):
    """EXISTS subquery: room has at least one blocked/booked night in [check_in, check_out)."""
    return exists().where(
        and_(
            Availability.room_id == Room.id,
            Availability.date >= check_in,
            Availability.date < check_out,
            Availability.status.in_(
                [AvailabilityStatus.blocked, AvailabilityStatus.booked]
            ),
        )
    )


@router.get("/hotels", response_model=list[HotelListItem])
async def list_hotels(
    city: str | None = Query(default=None),
    check_in: date | None = Query(default=None),
    check_out: date | None = Query(default=None),
    guests: int = Query(default=1, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
) -> list[HotelListItem]:
    _validate_date_range(check_in, check_out)

    # Subquery: rooms that fit (capacity OK) and (if dates given) are free.
    room_filter = [
        Room.hotel_id == Hotel.id,
        Room.capacity >= guests,
        Room.status == RoomStatus.published,
    ]
    if check_in and check_out:
        room_filter.append(not_(_room_unavailable_clause(check_in, check_out)))

    fit_rooms = (
        select(Room.hotel_id, func.min(Room.price_kgs).label("min_price"))
        .where(and_(*room_filter))
        .group_by(Room.hotel_id)
        .subquery()
    )

    stmt = (
        select(Hotel, fit_rooms.c.min_price)
        .join(fit_rooms, fit_rooms.c.hotel_id == Hotel.id)
        .where(Hotel.status == HotelStatus.published)
    )
    if city:
        stmt = stmt.where(Hotel.city.ilike(f"%{city}%"))

    rows = (await db.execute(stmt)).all()
    return [
        HotelListItem(
            id=h.id,
            slug=h.slug,
            name_ru=h.name_ru,
            name_ky=h.name_ky,
            name_en=h.name_en,
            description_ru=h.description_ru,
            description_ky=h.description_ky,
            description_en=h.description_en,
            city=h.city,
            address=h.address,
            photos=h.photos or [],
            meals=h.meals,
            min_price_kgs=min_price,
        )
        for h, min_price in rows
    ]


@router.get("/hotels/{slug_or_id}", response_model=HotelDetails)
async def hotel_details(
    slug_or_id: str,
    check_in: date | None = Query(default=None),
    check_out: date | None = Query(default=None),
    guests: int = Query(default=1, ge=1, le=20),
    beds: Literal["single", "double"] | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> HotelDetails:
    _validate_date_range(check_in, check_out)

    stmt = select(Hotel).where(Hotel.status == HotelStatus.published)
    if slug_or_id.isdigit():
        stmt = stmt.where(Hotel.id == int(slug_or_id))
    else:
        stmt = stmt.where(Hotel.slug == slug_or_id)
    hotel = (await db.execute(stmt)).scalar_one_or_none()
    if hotel is None:
        raise APIError(404, "not_found", "Hotel not found")
    hotel_id = hotel.id

    rooms = (
        (
            await db.execute(
                select(Room).where(
                    Room.hotel_id == hotel_id,
                    Room.status == RoomStatus.published,
                )
            )
        )
        .scalars()
        .all()
    )

    # capacity/beds filter (same shape as frontend «1+1» / «2 гостя» / family).
    def matches_beds(r: Room) -> bool:
        if beds == "single":
            return r.single_beds >= 2
        if beds == "double":
            return r.double_beds >= 1
        return r.capacity >= guests

    rooms = [r for r in rooms if matches_beds(r)]

    # Per-room availability + total_kgs for dates. Sourced even when no
    # filter applies so that surviving cards still get total_kgs_for_dates.
    cards: list[RoomCard] = []
    avail_by_room: dict[int, dict[date, Availability]] = {}
    if check_in and check_out and rooms:
        rows = (
            await db.execute(
                select(Availability).where(
                    Availability.room_id.in_([r.id for r in rooms]),
                    Availability.date >= check_in,
                    Availability.date < check_out,
                )
            )
        ).scalars().all()
        for av in rows:
            avail_by_room.setdefault(av.room_id, {})[av.date] = av

    for r in rooms:
        if check_in and check_out:
            day_map = avail_by_room.get(r.id, {})
            blocked = any(
                day_map.get(d) is not None
                and day_map[d].status in (AvailabilityStatus.blocked, AvailabilityStatus.booked)
                for d in date_range_nights(check_in, check_out)
            )
            if blocked:
                # Дропаем недоступные на эти даты — фронт уже не показывает
                # «Недоступно» disabled-карточки.
                continue
            available = True
            total = sum(
                (day_map[d].price_override if d in day_map and day_map[d].price_override is not None else r.price_kgs)
                for d in date_range_nights(check_in, check_out)
            )
        else:
            available = None
            total = None
        cards.append(
            RoomCard(
                id=r.id,
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
                available_for_dates=available,
                total_kgs_for_dates=total,
                amenities=r.amenities or [],
            )
        )

    services_rows = (
        (
            await db.execute(
                select(HotelService).where(HotelService.hotel_id == hotel_id).order_by(HotelService.id)
            )
        )
        .scalars()
        .all()
    )
    services = [
        ServicePublicView(
            id=s.id,
            name_ru=s.name_ru,
            name_ky=s.name_ky,
            name_en=s.name_en,
            price_kgs=s.price_kgs,
        )
        for s in services_rows
    ]

    return HotelDetails(
        id=hotel.id,
        slug=hotel.slug,
        name_ru=hotel.name_ru,
        name_ky=hotel.name_ky,
        name_en=hotel.name_en,
        description_ru=hotel.description_ru,
        description_ky=hotel.description_ky,
        description_en=hotel.description_en,
        city=hotel.city,
        address=hotel.address,
        lat=float(hotel.lat) if hotel.lat is not None else None,
        lng=float(hotel.lng) if hotel.lng is not None else None,
        photos=hotel.photos or [],
        meals=hotel.meals,
        amenities=hotel.amenities or [],
        checkin_time=hotel.checkin_time,
        checkout_time=hotel.checkout_time,
        rooms=cards,
        services=services,
    )
