from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, delete, exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext, require_role
from app.core.exceptions import APIError
from app.models.models import (
    Availability,
    AvailabilityStatus,
    Booking,
    BookingStatus,
    Hotel,
    HotelService,
    Room,
    User,
    UserRole,
)
from app.schemas.partner import (
    AvailabilityBatchUpdate,
    AvailabilityRowOut,
    HotelCreate,
    HotelPartnerView,
    HotelUpdate,
    PartnerBookingView,
    RoomCreate,
    RoomPartnerView,
    RoomUpdate,
    ServiceCreate,
    ServicePartnerView,
    ServiceUpdate,
)
from app.utils import gen_unique_hotel_slug

router = APIRouter(prefix="/p", tags=["partner"])


async def _get_my_hotel(db: AsyncSession, ctx: AuthContext, hotel_id: int) -> Hotel:
    hotel = (
        await db.execute(
            select(Hotel).where(Hotel.id == hotel_id, Hotel.owner_user_id == ctx.user.id)
        )
    ).scalar_one_or_none()
    if hotel is None:
        raise APIError(404, "not_found", "Hotel not found")
    return hotel


async def _get_my_room(
    db: AsyncSession, ctx: AuthContext, hotel_id: int, room_id: int
) -> Room:
    await _get_my_hotel(db, ctx, hotel_id)
    room = (
        await db.execute(
            select(Room).where(Room.id == room_id, Room.hotel_id == hotel_id)
        )
    ).scalar_one_or_none()
    if room is None:
        raise APIError(404, "not_found", "Room not found")
    return room


def _to_hotel_view(h: Hotel) -> HotelPartnerView:
    return HotelPartnerView(
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
        lat=float(h.lat) if h.lat is not None else None,
        lng=float(h.lng) if h.lng is not None else None,
        photos=h.photos or [],
        status=h.status,
        created_at=h.created_at,
        updated_at=h.updated_at,
    )


def _to_room_view(r: Room) -> RoomPartnerView:
    return RoomPartnerView(
        id=r.id,
        hotel_id=r.hotel_id,
        name_ru=r.name_ru,
        name_ky=r.name_ky,
        name_en=r.name_en,
        description_ru=r.description_ru,
        description_ky=r.description_ky,
        description_en=r.description_en,
        capacity=r.capacity,
        price_kgs=r.price_kgs,
        floor=r.floor,
        photos=r.photos or [],
        created_at=r.created_at,
    )


# ─── Hotels ────────────────────────────────────────────────────────────────

@router.get("/hotels", response_model=list[HotelPartnerView])
async def list_my_hotels(
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        (
            await db.execute(
                select(Hotel)
                .where(Hotel.owner_user_id == ctx.user.id)
                .order_by(Hotel.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_to_hotel_view(h) for h in rows]


@router.post("/hotels", response_model=HotelPartnerView, status_code=201)
async def create_hotel(
    payload: HotelCreate,
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    h = Hotel(
        owner_user_id=ctx.user.id,
        slug="__pending__",  # placeholder; replaced after flush() gives id
        name_ru=payload.name_ru,
        name_ky=payload.name_ky,
        name_en=payload.name_en,
        description_ru=payload.description_ru,
        description_ky=payload.description_ky,
        description_en=payload.description_en,
        city=payload.city,
        address=payload.address,
        lat=payload.lat,
        lng=payload.lng,
        photos=payload.photos,
    )
    db.add(h)
    await db.flush()  # need h.id for slug fallback
    h.slug = await gen_unique_hotel_slug(db, payload.name_en, h.id, exclude_id=h.id)
    await db.commit()
    await db.refresh(h)
    return _to_hotel_view(h)


@router.get("/hotels/{hotel_id}", response_model=HotelPartnerView)
async def get_my_hotel(
    hotel_id: int,
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    return _to_hotel_view(await _get_my_hotel(db, ctx, hotel_id))


@router.put("/hotels/{hotel_id}", response_model=HotelPartnerView)
async def update_hotel(
    hotel_id: int,
    payload: HotelUpdate,
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    h = await _get_my_hotel(db, ctx, hotel_id)
    data = payload.model_dump(exclude_unset=True)
    name_en_changed = "name_en" in data and data["name_en"] != h.name_en
    for field, value in data.items():
        setattr(h, field, value)
    if name_en_changed:
        h.slug = await gen_unique_hotel_slug(db, h.name_en, h.id, exclude_id=h.id)
    await db.commit()
    await db.refresh(h)
    return _to_hotel_view(h)


@router.delete("/hotels/{hotel_id}", status_code=204)
async def delete_hotel(
    hotel_id: int,
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    h = await _get_my_hotel(db, ctx, hotel_id)
    has_bookings = (
        await db.execute(
            select(exists().where(
                and_(Room.hotel_id == h.id, Booking.room_id == Room.id)
            ))
        )
    ).scalar()
    if has_bookings:
        raise APIError(409, "conflict", "Hotel has bookings; cannot hard-delete")
    await db.delete(h)
    await db.commit()
    return None


# ─── Rooms (nested) ────────────────────────────────────────────────────────

@router.get("/hotels/{hotel_id}/rooms", response_model=list[RoomPartnerView])
async def list_rooms(
    hotel_id: int,
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    await _get_my_hotel(db, ctx, hotel_id)
    rows = (
        (
            await db.execute(
                select(Room).where(Room.hotel_id == hotel_id).order_by(Room.id)
            )
        )
        .scalars()
        .all()
    )
    return [_to_room_view(r) for r in rows]


@router.post("/hotels/{hotel_id}/rooms", response_model=RoomPartnerView, status_code=201)
async def create_room(
    hotel_id: int,
    payload: RoomCreate,
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    await _get_my_hotel(db, ctx, hotel_id)
    r = Room(hotel_id=hotel_id, **payload.model_dump())
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return _to_room_view(r)


@router.get("/hotels/{hotel_id}/rooms/{room_id}", response_model=RoomPartnerView)
async def get_room(
    hotel_id: int,
    room_id: int,
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    return _to_room_view(await _get_my_room(db, ctx, hotel_id, room_id))


async def _room_has_active_bookings(db: AsyncSession, room_id: int) -> bool:
    today = date.today()
    return bool(
        (
            await db.execute(
                select(exists().where(
                    and_(
                        Booking.room_id == room_id,
                        Booking.status.in_([BookingStatus.pending, BookingStatus.paid]),
                        Booking.check_out >= today,
                    )
                ))
            )
        ).scalar()
    )


@router.put("/hotels/{hotel_id}/rooms/{room_id}", response_model=RoomPartnerView)
async def update_room(
    hotel_id: int,
    room_id: int,
    payload: RoomUpdate,
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    r = await _get_my_room(db, ctx, hotel_id, room_id)
    data = payload.model_dump(exclude_unset=True)
    if "capacity" in data and data["capacity"] != r.capacity:
        if await _room_has_active_bookings(db, r.id):
            raise APIError(
                409, "conflict", "Cannot change capacity while active bookings exist"
            )
    for field, value in data.items():
        setattr(r, field, value)
    await db.commit()
    await db.refresh(r)
    return _to_room_view(r)


@router.delete("/hotels/{hotel_id}/rooms/{room_id}", status_code=204)
async def delete_room(
    hotel_id: int,
    room_id: int,
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    r = await _get_my_room(db, ctx, hotel_id, room_id)
    if await _room_has_active_bookings(db, r.id):
        raise APIError(409, "conflict", "Room has active bookings")
    await db.delete(r)
    await db.commit()
    return None


# ─── Availability ──────────────────────────────────────────────────────────

@router.get(
    "/hotels/{hotel_id}/rooms/{room_id}/availability",
    response_model=list[AvailabilityRowOut],
)
async def get_availability(
    hotel_id: int,
    room_id: int,
    from_: date = Query(alias="from"),
    to: date = Query(...),
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    await _get_my_room(db, ctx, hotel_id, room_id)
    if to <= from_:
        raise APIError(400, "bad_request", "to must be after from")
    rows = (
        (
            await db.execute(
                select(Availability)
                .where(
                    Availability.room_id == room_id,
                    Availability.date >= from_,
                    Availability.date < to,
                )
                .order_by(Availability.date)
            )
        )
        .scalars()
        .all()
    )
    return [
        AvailabilityRowOut(date=a.date, status=a.status, price_override=a.price_override)
        for a in rows
    ]


@router.put(
    "/hotels/{hotel_id}/rooms/{room_id}/availability",
    response_model=list[AvailabilityRowOut],
)
async def update_availability(
    hotel_id: int,
    room_id: int,
    payload: AvailabilityBatchUpdate,
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    await _get_my_room(db, ctx, hotel_id, room_id)

    # Reject any attempt to set 'booked' through partner endpoint.
    for n in payload.nights:
        if n.status == AvailabilityStatus.booked:
            raise APIError(
                400,
                "bad_request",
                f"Cannot set status=booked manually (date {n.date.isoformat()})",
            )

    # Refuse to overwrite existing 'booked' rows.
    incoming_dates = [n.date for n in payload.nights]
    if incoming_dates:
        existing_booked = (
            (
                await db.execute(
                    select(Availability.date).where(
                        Availability.room_id == room_id,
                        Availability.date.in_(incoming_dates),
                        Availability.status == AvailabilityStatus.booked,
                    )
                )
            )
            .scalars()
            .all()
        )
        if existing_booked:
            raise APIError(
                409,
                "conflict",
                f"Cannot modify booked nights: {[d.isoformat() for d in existing_booked]}",
            )

    # Apply: status=free && price_override=None → delete row; else upsert.
    for n in payload.nights:
        if n.status == AvailabilityStatus.free and n.price_override is None:
            await db.execute(
                delete(Availability).where(
                    Availability.room_id == room_id, Availability.date == n.date
                )
            )
            continue
        stmt = (
            pg_insert(Availability)
            .values(
                room_id=room_id,
                date=n.date,
                status=n.status,
                price_override=n.price_override,
            )
            .on_conflict_do_update(
                index_elements=["room_id", "date"],
                set_={"status": n.status, "price_override": n.price_override},
            )
        )
        await db.execute(stmt)
    await db.commit()

    if incoming_dates:
        rows = (
            (
                await db.execute(
                    select(Availability)
                    .where(
                        Availability.room_id == room_id,
                        Availability.date.in_(incoming_dates),
                    )
                    .order_by(Availability.date)
                )
            )
            .scalars()
            .all()
        )
    else:
        rows = []
    return [
        AvailabilityRowOut(date=a.date, status=a.status, price_override=a.price_override)
        for a in rows
    ]


# ─── Services ──────────────────────────────────────────────────────────────

def _to_service_view(s: HotelService) -> ServicePartnerView:
    return ServicePartnerView(
        id=s.id,
        hotel_id=s.hotel_id,
        name_ru=s.name_ru,
        name_ky=s.name_ky,
        name_en=s.name_en,
        price_kgs=s.price_kgs,
        created_at=s.created_at,
    )


async def _get_my_service(
    db: AsyncSession, ctx: AuthContext, hotel_id: int, service_id: int
) -> HotelService:
    await _get_my_hotel(db, ctx, hotel_id)
    s = (
        await db.execute(
            select(HotelService).where(
                HotelService.id == service_id, HotelService.hotel_id == hotel_id
            )
        )
    ).scalar_one_or_none()
    if s is None:
        raise APIError(404, "not_found", "Service not found")
    return s


@router.get("/hotels/{hotel_id}/services", response_model=list[ServicePartnerView])
async def list_services(
    hotel_id: int,
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    await _get_my_hotel(db, ctx, hotel_id)
    rows = (
        (
            await db.execute(
                select(HotelService).where(HotelService.hotel_id == hotel_id).order_by(HotelService.id)
            )
        )
        .scalars()
        .all()
    )
    return [_to_service_view(s) for s in rows]


@router.post("/hotels/{hotel_id}/services", response_model=ServicePartnerView, status_code=201)
async def create_service(
    hotel_id: int,
    payload: ServiceCreate,
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    await _get_my_hotel(db, ctx, hotel_id)
    s = HotelService(hotel_id=hotel_id, **payload.model_dump())
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return _to_service_view(s)


@router.put("/hotels/{hotel_id}/services/{service_id}", response_model=ServicePartnerView)
async def update_service(
    hotel_id: int,
    service_id: int,
    payload: ServiceUpdate,
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    s = await _get_my_service(db, ctx, hotel_id, service_id)
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(s, field, value)
    await db.commit()
    await db.refresh(s)
    return _to_service_view(s)


@router.delete("/hotels/{hotel_id}/services/{service_id}", status_code=204)
async def delete_service(
    hotel_id: int,
    service_id: int,
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    s = await _get_my_service(db, ctx, hotel_id, service_id)
    await db.delete(s)
    await db.commit()
    return None


# ─── Incoming bookings ─────────────────────────────────────────────────────

@router.get("/bookings", response_model=list[PartnerBookingView])
async def list_incoming_bookings(
    status_filter: BookingStatus | None = Query(default=None, alias="status"),
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Booking, Room, Hotel, User)
        .join(Room, Room.id == Booking.room_id)
        .join(Hotel, Hotel.id == Room.hotel_id)
        .join(User, User.id == Booking.user_id)
        .where(Hotel.owner_user_id == ctx.user.id)
        .order_by(Booking.created_at.desc())
        .limit(200)
    )
    if status_filter is not None:
        stmt = stmt.where(Booking.status == status_filter)

    rows = (await db.execute(stmt)).all()
    return [
        PartnerBookingView(
            id=b.id,
            code=b.code,
            room_id=r.id,
            room_name_ru=r.name_ru,
            hotel_id=h.id,
            hotel_name_ru=h.name_ru,
            client_first_name=u.first_name,
            check_in=b.check_in,
            check_out=b.check_out,
            guests=b.guests,
            total_kgs=b.total_kgs,
            status=b.status,
            created_at=b.created_at,
        )
        for b, r, h, u in rows
    ]


async def _get_my_booking(
    db: AsyncSession, ctx: AuthContext, code: str
) -> tuple[Booking, Room, Hotel, User]:
    row = (
        await db.execute(
            select(Booking, Room, Hotel, User)
            .join(Room, Room.id == Booking.room_id)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .join(User, User.id == Booking.user_id)
            .where(Booking.code == code, Hotel.owner_user_id == ctx.user.id)
            .with_for_update(of=Booking)
        )
    ).first()
    if row is None:
        raise APIError(404, "not_found", "Booking not found")
    return row


def _to_partner_booking(b: Booking, r: Room, h: Hotel, u: User) -> PartnerBookingView:
    return PartnerBookingView(
        id=b.id,
        code=b.code,
        room_id=r.id,
        room_name_ru=r.name_ru,
        hotel_id=h.id,
        hotel_name_ru=h.name_ru,
        client_first_name=u.first_name,
        check_in=b.check_in,
        check_out=b.check_out,
        guests=b.guests,
        total_kgs=b.total_kgs,
        status=b.status,
        created_at=b.created_at,
    )


@router.post("/bookings/{code}/confirm", response_model=PartnerBookingView)
async def confirm_booking(
    code: str,
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    b, r, h, u = await _get_my_booking(db, ctx, code)
    if b.status != BookingStatus.pending:
        raise APIError(409, "conflict", f"Booking is {b.status.value}, only pending can be confirmed")
    b.status = BookingStatus.paid
    await db.commit()
    await db.refresh(b)
    return _to_partner_booking(b, r, h, u)


@router.post("/bookings/{code}/cancel", response_model=PartnerBookingView)
async def cancel_booking(
    code: str,
    ctx: AuthContext = Depends(require_role(UserRole.partner)),
    db: AsyncSession = Depends(get_db),
):
    b, r, h, u = await _get_my_booking(db, ctx, code)
    if b.status in (BookingStatus.cancelled, BookingStatus.refunded):
        raise APIError(409, "conflict", f"Booking is already {b.status.value}")

    avail_rows = (
        (
            await db.execute(
                select(Availability)
                .where(
                    Availability.room_id == b.room_id,
                    Availability.date >= b.check_in,
                    Availability.date < b.check_out,
                    Availability.status == AvailabilityStatus.booked,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for a in avail_rows:
        if a.price_override is None:
            await db.execute(
                delete(Availability).where(
                    Availability.room_id == a.room_id, Availability.date == a.date
                )
            )
        else:
            a.status = AvailabilityStatus.free

    b.status = BookingStatus.cancelled
    await db.commit()
    await db.refresh(b)
    return _to_partner_booking(b, r, h, u)
