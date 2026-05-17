from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pubsub
from app.core.database import get_db
from app.core.deps import AuthContext, require_verified_partner
from app.core.exceptions import APIError
from app.models.models import (
    Availability,
    AvailabilityStatus,
    Booking,
    BookingStatus,
    Client,
    Hotel,
    HotelService,
    HotelStatus,
    Room,
    User,
    UserRole,
)
from app.schemas.partner import (
    AvailabilityBatchUpdate,
    AvailabilityRowOut,
    ClientLookup,
    ClientPartnerView,
    ClientUpdate,
    ChecklistAction,
    ChecklistItem,
    HotelCreate,
    HotelDashboard,
    HotelPartnerView,
    HotelStats,
    HotelUpdate,
    PartnerBookingView,
    RoomCreate,
    RoomFlatView,
    RoomPartnerView,
    RoomUpdate,
    ServiceCreate,
    ServicePartnerView,
    ServiceUpdate,
    WalkinBookingCreate,
)
from app.utils import (
    date_range_nights,
    gen_booking_code,
    gen_unique_hotel_slug,
    normalize_email,
    normalize_phone,
)

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
        published_at=h.published_at,
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
        beds=r.beds,
        photos=r.photos or [],
        created_at=r.created_at,
    )


# ─── Hotels ────────────────────────────────────────────────────────────────

@router.get("/hotels", response_model=list[HotelPartnerView])
async def list_my_hotels(
    ctx: AuthContext = Depends(require_verified_partner),
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
    ctx: AuthContext = Depends(require_verified_partner),
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
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    return _to_hotel_view(await _get_my_hotel(db, ctx, hotel_id))


@router.get("/hotels/{hotel_id}/dashboard", response_model=HotelDashboard)
async def get_hotel_dashboard(
    hotel_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    h = await _get_my_hotel(db, ctx, hotel_id)
    rooms = (
        (
            await db.execute(
                select(Room).where(Room.hotel_id == hotel_id).order_by(Room.id)
            )
        )
        .scalars()
        .all()
    )

    checks = _build_checklist(h, rooms)
    required_fails = sum(1 for c in checks if c.kind == "required" and not c.ok)
    stats = await _compute_hotel_stats(db, hotel_id)
    return HotelDashboard(
        can_publish=required_fails == 0,
        checks=checks,
        stats=stats,
    )


def _build_checklist(h: Hotel, rooms: list[Room]) -> list[ChecklistItem]:
    photo_count = len(h.photos or [])
    no_price_rooms = [r for r in rooms if not r.price_kgs or r.price_kgs <= 0]
    no_photo_rooms = [r for r in rooms if not (r.photos or [])]
    has_desc = any(
        (d or "").strip().__len__() >= 20
        for d in (h.description_ru, h.description_ky, h.description_en)
    )

    out: list[ChecklistItem] = []
    out.append(ChecklistItem(
        kind="required",
        ok=photo_count > 0,
        key="status.check.hotel_photos",
        action=None if photo_count > 0 else ChecklistAction(tab="photos"),
    ))
    out.append(ChecklistItem(
        kind="required",
        ok=len(rooms) > 0,
        key="status.check.has_rooms",
        action=None if rooms else ChecklistAction(nav="rooms"),
    ))
    if no_price_rooms:
        out.append(ChecklistItem(
            kind="required",
            ok=False,
            key="status.check.rooms_price_missing",
            params={"n": len(no_price_rooms)},
            action=ChecklistAction(room_id=no_price_rooms[0].id),
        ))
    else:
        out.append(ChecklistItem(
            kind="required",
            ok=len(rooms) > 0,
            key="status.check.rooms_price_ok",
        ))

    out.append(ChecklistItem(
        kind="recommended",
        ok=has_desc,
        key="status.check.description",
        action=None if has_desc else ChecklistAction(tab="description"),
    ))
    out.append(ChecklistItem(
        kind="recommended",
        ok=bool(h.address and h.address.strip()),
        key="status.check.address",
        action=None if (h.address and h.address.strip()) else ChecklistAction(tab="description"),
    ))
    out.append(ChecklistItem(
        kind="recommended",
        ok=h.lat is not None and h.lng is not None,
        key="status.check.coords",
        action=None if (h.lat is not None and h.lng is not None) else ChecklistAction(tab="description"),
    ))
    out.append(ChecklistItem(
        kind="recommended",
        ok=bool(h.name_ky and h.name_en),
        key="status.check.name_translations",
        action=None if (h.name_ky and h.name_en) else ChecklistAction(tab="description"),
    ))
    if rooms:
        if no_photo_rooms:
            out.append(ChecklistItem(
                kind="recommended",
                ok=False,
                key="status.check.rooms_no_photos",
                params={"n": len(no_photo_rooms)},
                action=ChecklistAction(room_id=no_photo_rooms[0].id),
            ))
        else:
            out.append(ChecklistItem(
                kind="recommended",
                ok=True,
                key="status.check.rooms_photos_ok",
            ))

    return out


async def _compute_hotel_stats(db: AsyncSession, hotel_id: int) -> HotelStats:
    today = date.today()
    in_7d = today + timedelta(days=7)
    since_30d = datetime.now(timezone.utc) - timedelta(days=30)

    room_ids_subq = select(Room.id).where(Room.hotel_id == hotel_id).scalar_subquery()

    is_active = case(
        (
            and_(
                Booking.status.in_([BookingStatus.pending, BookingStatus.paid]),
                Booking.check_out >= today,
            ),
            1,
        ),
        else_=0,
    )
    is_checkin_7d = case(
        (
            and_(
                Booking.status.not_in([BookingStatus.cancelled, BookingStatus.refunded]),
                Booking.check_in >= today,
                Booking.check_in <= in_7d,
            ),
            1,
        ),
        else_=0,
    )
    revenue_expr = case(
        (
            and_(
                Booking.status == BookingStatus.paid,
                Booking.created_at >= since_30d,
            ),
            Booking.total_kgs,
        ),
        else_=0,
    )

    row = (
        await db.execute(
            select(
                func.count(Booking.id).label("total"),
                func.coalesce(func.sum(is_active), 0).label("active"),
                func.coalesce(func.sum(is_checkin_7d), 0).label("checkins7d"),
                func.coalesce(func.sum(revenue_expr), 0).label("revenue30d"),
                func.max(Booking.created_at).label("last_at"),
            ).where(Booking.room_id.in_(room_ids_subq))
        )
    ).one()

    return HotelStats(
        bookings_total=row.total or 0,
        bookings_active=int(row.active or 0),
        checkins_next_7d=int(row.checkins7d or 0),
        revenue_kgs_30d=int(row.revenue30d or 0),
        last_booking_at=row.last_at,
    )


@router.put("/hotels/{hotel_id}", response_model=HotelPartnerView)
async def update_hotel(
    hotel_id: int,
    payload: HotelUpdate,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    h = await _get_my_hotel(db, ctx, hotel_id)
    data = payload.model_dump(exclude_unset=True)
    name_en_changed = "name_en" in data and data["name_en"] != h.name_en
    new_status = data.get("status")
    becomes_published = (
        "status" in data
        and new_status == HotelStatus.published
        and h.status != HotelStatus.published
    )
    for field, value in data.items():
        setattr(h, field, value)
    if name_en_changed:
        h.slug = await gen_unique_hotel_slug(db, h.name_en, h.id, exclude_id=h.id)
    if becomes_published:
        h.published_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(h)
    return _to_hotel_view(h)


@router.delete("/hotels/{hotel_id}", status_code=204)
async def delete_hotel(
    hotel_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    h = await _get_my_hotel(db, ctx, hotel_id)
    today = date.today()
    has_active = (
        await db.execute(
            select(exists().where(
                and_(
                    Room.hotel_id == h.id,
                    Booking.room_id == Room.id,
                    Booking.status.in_([BookingStatus.pending, BookingStatus.paid]),
                    Booking.check_out >= today,
                )
            ))
        )
    ).scalar()
    if has_active:
        raise APIError(409, "conflict", "Hotel has active bookings; cannot hard-delete")
    await db.execute(
        delete(Booking).where(
            Booking.room_id.in_(select(Room.id).where(Room.hotel_id == h.id))
        )
    )
    await db.delete(h)
    await db.commit()
    return None


# ─── Rooms (nested) ────────────────────────────────────────────────────────

@router.get("/hotels/{hotel_id}/rooms", response_model=list[RoomPartnerView])
async def list_rooms(
    hotel_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
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
    ctx: AuthContext = Depends(require_verified_partner),
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
    ctx: AuthContext = Depends(require_verified_partner),
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
    ctx: AuthContext = Depends(require_verified_partner),
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
    ctx: AuthContext = Depends(require_verified_partner),
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
    ctx: AuthContext = Depends(require_verified_partner),
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
    ctx: AuthContext = Depends(require_verified_partner),
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
    ctx: AuthContext = Depends(require_verified_partner),
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
    ctx: AuthContext = Depends(require_verified_partner),
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
    ctx: AuthContext = Depends(require_verified_partner),
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
    ctx: AuthContext = Depends(require_verified_partner),
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
    hotel_id: int | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Booking, Room, Hotel, Client)
        .join(Room, Room.id == Booking.room_id)
        .join(Hotel, Hotel.id == Room.hotel_id)
        .join(Client, Client.id == Booking.client_id)
        .where(Hotel.owner_user_id == ctx.user.id)
        .order_by(Booking.created_at.desc())
        .limit(limit)
    )
    if status_filter is not None:
        stmt = stmt.where(Booking.status == status_filter)
    if hotel_id is not None:
        stmt = stmt.where(Hotel.id == hotel_id)

    rows = (await db.execute(stmt)).all()
    return [
        PartnerBookingView(
            id=b.id,
            code=b.code,
            room_id=r.id,
            room_name_ru=r.name_ru,
            hotel_id=h.id,
            hotel_name_ru=h.name_ru,
            client_first_name=c.first_name,
            check_in=b.check_in,
            check_out=b.check_out,
            guests=b.guests,
            total_kgs=b.total_kgs,
            status=b.status,
            created_at=b.created_at,
        )
        for b, r, h, c in rows
    ]


async def _get_my_booking(
    db: AsyncSession, ctx: AuthContext, code: str
) -> tuple[Booking, Room, Hotel, Client]:
    row = (
        await db.execute(
            select(Booking, Room, Hotel, Client)
            .join(Room, Room.id == Booking.room_id)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .join(Client, Client.id == Booking.client_id)
            .where(Booking.code == code, Hotel.owner_user_id == ctx.user.id)
            .with_for_update(of=Booking)
        )
    ).first()
    if row is None:
        raise APIError(404, "not_found", "Booking not found")
    return row


def _to_partner_booking(b: Booking, r: Room, h: Hotel, c: Client) -> PartnerBookingView:
    return PartnerBookingView(
        id=b.id,
        code=b.code,
        room_id=r.id,
        room_name_ru=r.name_ru,
        hotel_id=h.id,
        hotel_name_ru=h.name_ru,
        client_first_name=c.first_name,
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
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    b, r, h, c = await _get_my_booking(db, ctx, code)
    if b.status != BookingStatus.pending:
        raise APIError(409, "conflict", f"Booking is {b.status.value}, only pending can be confirmed")
    b.status = BookingStatus.paid
    hotel_id_for_pub = h.id
    await db.commit()
    await db.refresh(b)
    await pubsub.publish_refresh(hotel_id_for_pub)
    return _to_partner_booking(b, r, h, c)


@router.post("/bookings/{code}/cancel", response_model=PartnerBookingView)
async def cancel_booking(
    code: str,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    b, r, h, c = await _get_my_booking(db, ctx, code)
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
    hotel_id_for_pub = h.id
    await db.commit()
    await db.refresh(b)
    await pubsub.publish_refresh(hotel_id_for_pub)
    return _to_partner_booking(b, r, h, c)


# ─── Walk-in bookings ─────────────────────────────────────────────────────

async def _find_or_create_client_for_walkin(
    db: AsyncSession, payload: WalkinBookingCreate
) -> Client:
    """Dedup walk-in clients by normalized phone / email; create otherwise."""
    norm_phone = normalize_phone(payload.phone)
    norm_email = normalize_email(payload.email)
    existing: Client | None = None
    if norm_phone:
        existing = (
            await db.execute(select(Client).where(Client.phone == norm_phone))
        ).scalar_one_or_none()
    if existing is None and norm_email:
        existing = (
            await db.execute(select(Client).where(Client.email == norm_email))
        ).scalar_one_or_none()
    if existing is not None:
        return existing
    c = Client(
        first_name=payload.first_name,
        last_name=payload.last_name,
        phone=norm_phone,
        email=norm_email,
        doc_kind=payload.doc_kind,
        doc_number=payload.doc_number,
    )
    db.add(c)
    await db.flush()
    return c


@router.post("/walkin-bookings", response_model=PartnerBookingView, status_code=201)
async def create_walkin_booking(
    payload: WalkinBookingCreate,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    if payload.check_out <= payload.check_in:
        raise APIError(400, "bad_request", "check_out must be after check_in")
    today = date.today()
    if payload.check_in < today:
        raise APIError(400, "bad_request", "check_in is in the past")

    # Verify room belongs to this partner's hotel + lock it.
    row = (
        await db.execute(
            select(Room, Hotel)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .where(Room.id == payload.room_id, Hotel.owner_user_id == ctx.user.id)
            .with_for_update(of=Room)
        )
    ).first()
    if row is None:
        raise APIError(404, "not_found", "Room not found")
    room, hotel = row
    if room.capacity < payload.guests:
        raise APIError(400, "bad_request", "Too many guests for this room")

    # Lock availability rows in range; verify nothing is blocked/booked.
    nights = list(date_range_nights(payload.check_in, payload.check_out))
    existing_av = (
        await db.execute(
            select(Availability)
            .where(
                Availability.room_id == room.id,
                Availability.date >= payload.check_in,
                Availability.date < payload.check_out,
            )
            .with_for_update()
        )
    ).scalars().all()
    by_date = {a.date: a for a in existing_av}
    for d in nights:
        a = by_date.get(d)
        if a is not None and a.status in (AvailabilityStatus.blocked, AvailabilityStatus.booked):
            raise APIError(409, "conflict", f"Night {d.isoformat()} is not available")

    total = 0
    for d in nights:
        a = by_date.get(d)
        total += a.price_override if (a and a.price_override is not None) else room.price_kgs

    for d in nights:
        stmt = (
            pg_insert(Availability)
            .values(room_id=room.id, date=d, status=AvailabilityStatus.booked)
            .on_conflict_do_update(
                index_elements=["room_id", "date"],
                set_={"status": AvailabilityStatus.booked},
            )
        )
        await db.execute(stmt)

    client = await _find_or_create_client_for_walkin(db, payload)

    for _ in range(5):
        code = gen_booking_code()
        clash = (
            await db.execute(select(Booking.id).where(Booking.code == code))
        ).scalar_one_or_none()
        if clash is None:
            break
    else:
        raise APIError(500, "internal", "Failed to generate booking code")

    booking = Booking(
        code=code,
        client_id=client.id,
        room_id=room.id,
        check_in=payload.check_in,
        check_out=payload.check_out,
        guests=payload.guests,
        total_kgs=total,
        status=BookingStatus.pending,
    )
    db.add(booking)
    hotel_id_for_pub = hotel.id
    await db.commit()
    await db.refresh(booking)
    await pubsub.publish_refresh(hotel_id_for_pub)
    return _to_partner_booking(booking, room, hotel, client)


# ─── /p/rooms (flat list with today_status) ────────────────────────────────

@router.get("/rooms", response_model=list[RoomFlatView])
async def list_all_my_rooms(
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    today = date.today()
    rows = (
        await db.execute(
            select(Room, Hotel)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .where(Hotel.owner_user_id == ctx.user.id)
            .order_by(Hotel.name_ru, Room.id)
        )
    ).all()
    if not rows:
        return []

    room_ids = [r.id for r, _ in rows]
    av_rows = (
        await db.execute(
            select(Availability)
            .where(Availability.room_id.in_(room_ids), Availability.date == today)
        )
    ).scalars().all()
    today_by_room = {a.room_id: a.status for a in av_rows}

    out: list[RoomFlatView] = []
    for r, h in rows:
        photo = (r.photos or [None])[0] if r.photos else None
        out.append(RoomFlatView(
            room_id=r.id,
            room_name_ru=r.name_ru,
            hotel_id=h.id,
            hotel_name_ru=h.name_ru,
            capacity=r.capacity,
            beds=r.beds,
            floor=r.floor,
            price_kgs=r.price_kgs,
            today_status=today_by_room.get(r.id, AvailabilityStatus.free),
            photo=photo,
        ))
    return out


# ─── /p/clients ────────────────────────────────────────────────────────────

async def _client_visible_to_me(
    db: AsyncSession, partner_user_id: int, client_id: int
) -> Client | None:
    """A client is visible to a partner only if they have a booking in one
    of the partner's hotels."""
    stmt = (
        select(Client)
        .join(Booking, Booking.client_id == Client.id)
        .join(Room, Room.id == Booking.room_id)
        .join(Hotel, Hotel.id == Room.hotel_id)
        .where(Client.id == client_id, Hotel.owner_user_id == partner_user_id)
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _to_client_view(c: Client, bookings_count: int, last_date: date | None) -> ClientPartnerView:
    return ClientPartnerView(
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
        last_booking_date=last_date,
        created_at=c.created_at,
    )


@router.get("/clients", response_model=list[ClientPartnerView])
async def list_my_clients(
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    """All clients who have at least one booking in any of my hotels.
    bookings_count / last_booking_date are scoped to MY hotels only."""
    from sqlalchemy import func as sa_func
    stmt = (
        select(
            Client,
            sa_func.count(Booking.id).label("cnt"),
            sa_func.max(Booking.check_in).label("last_date"),
        )
        .join(Booking, Booking.client_id == Client.id)
        .join(Room, Room.id == Booking.room_id)
        .join(Hotel, Hotel.id == Room.hotel_id)
        .where(Hotel.owner_user_id == ctx.user.id)
        .group_by(Client.id)
        .order_by(sa_func.max(Booking.created_at).desc())
        .limit(500)
    )
    rows = (await db.execute(stmt)).all()
    return [_to_client_view(c, cnt, last) for (c, cnt, last) in rows]


@router.post("/clients/lookup", response_model=ClientPartnerView | None)
async def lookup_client(
    payload: ClientLookup,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    """For the walk-in form: find existing client by phone or email so the
    partner can pre-fill. Returns the global record (scope-agnostic). Returns
    null if nothing matched."""
    norm_phone = normalize_phone(payload.phone)
    norm_email = normalize_email(payload.email)
    if not norm_phone and not norm_email:
        return None
    c: Client | None = None
    if norm_phone:
        c = (await db.execute(select(Client).where(Client.phone == norm_phone))).scalar_one_or_none()
    if c is None and norm_email:
        c = (await db.execute(select(Client).where(Client.email == norm_email))).scalar_one_or_none()
    if c is None:
        return None
    return _to_client_view(c, bookings_count=0, last_date=None)


@router.get("/clients/{client_id}", response_model=ClientPartnerView)
async def get_my_client(
    client_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    c = await _client_visible_to_me(db, ctx.user.id, client_id)
    if c is None:
        raise APIError(404, "not_found", "Client not found")
    from sqlalchemy import func as sa_func
    cnt, last = (
        await db.execute(
            select(sa_func.count(Booking.id), sa_func.max(Booking.check_in))
            .join(Room, Room.id == Booking.room_id)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .where(Booking.client_id == c.id, Hotel.owner_user_id == ctx.user.id)
        )
    ).one()
    return _to_client_view(c, cnt or 0, last)


@router.get("/clients/{client_id}/bookings", response_model=list[PartnerBookingView])
async def list_my_client_bookings(
    client_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    c = await _client_visible_to_me(db, ctx.user.id, client_id)
    if c is None:
        raise APIError(404, "not_found", "Client not found")
    rows = (
        await db.execute(
            select(Booking, Room, Hotel)
            .join(Room, Room.id == Booking.room_id)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .where(Booking.client_id == c.id, Hotel.owner_user_id == ctx.user.id)
            .order_by(Booking.created_at.desc())
        )
    ).all()
    return [_to_partner_booking(b, r, h, c) for (b, r, h) in rows]


@router.put("/clients/{client_id}", response_model=ClientPartnerView)
async def update_my_client(
    client_id: int,
    payload: ClientUpdate,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    c = await _client_visible_to_me(db, ctx.user.id, client_id)
    if c is None:
        raise APIError(404, "not_found", "Client not found")
    data = payload.model_dump(exclude_unset=True)
    if "phone" in data:
        data["phone"] = normalize_phone(data["phone"])
    if "email" in data:
        data["email"] = normalize_email(data["email"])
    for k, v in data.items():
        setattr(c, k, v)
    await db.commit()
    await db.refresh(c)
    return _to_client_view(c, bookings_count=0, last_date=None)
