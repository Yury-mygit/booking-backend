"""Partner rooms: CRUD + availability + flat-list для today_status.

Availability: батч-обновление по диапазону дат с `pg_insert ON CONFLICT`.
`/p/rooms` (без hotel_id) — плоский список всех комнат всех accessible
owners со статусом «сегодня» для квик-обзора.
"""
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, delete, exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext, require_verified_partner
from app.core.exceptions import APIError
from app.core.audit import audit
from app.services import scope
from app.models.models import (
    Availability,
    AvailabilityStatus,
    Booking,
    BookingStatus,
    Hotel,
    Room,
)
from app.schemas.partner import (
    AvailabilityBatchUpdate,
    AvailabilityRowOut,
    RoomCreate,
    RoomFlatView,
    RoomPartnerView,
    RoomUpdate,
)

router = APIRouter()  # prefix задан в partner/__init__.py


# ─── Rooms (nested) ────────────────────────────────────────────────────────

@router.get("/hotels/{hotel_id}/rooms", response_model=list[RoomPartnerView])
async def list_rooms(
    hotel_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    await scope.get_my_hotel(db, ctx, hotel_id)
    rows = (
        (
            await db.execute(
                select(Room).where(Room.hotel_id == hotel_id).order_by(Room.id)
            )
        )
        .scalars()
        .all()
    )
    return [RoomPartnerView.from_model(r) for r in rows]


@router.post("/hotels/{hotel_id}/rooms", response_model=RoomPartnerView, status_code=201)
async def create_room(
    hotel_id: int,
    payload: RoomCreate,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    h = await scope.get_my_hotel(db, ctx, hotel_id, require_perm="manage_rooms")
    r = Room(hotel_id=hotel_id, **payload.model_dump())
    db.add(r)
    await db.commit()
    await db.refresh(r)
    await audit(
        db, ctx,
        owner_user_id=h.owner_user_id,
        action="room.create",
        subject_type="room",
        subject_id=r.id,
        payload={"hotel_id": hotel_id, "name_ru": r.name_ru, "capacity": r.capacity, "price_kgs": r.price_kgs},
    )
    return RoomPartnerView.from_model(r)


@router.get("/hotels/{hotel_id}/rooms/{room_id}", response_model=RoomPartnerView)
async def get_room(
    hotel_id: int,
    room_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    return RoomPartnerView.from_model(await scope.get_my_room(db, ctx, room_id, hotel_id=hotel_id))


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
    r = await scope.get_my_room(db, ctx, room_id, hotel_id=hotel_id, require_perm="manage_rooms")
    data = payload.model_dump(exclude_unset=True)
    if "capacity" in data and data["capacity"] != r.capacity:
        if await _room_has_active_bookings(db, r.id):
            raise APIError(
                409, "conflict", "Cannot change capacity while active bookings exist"
            )
    for field, value in data.items():
        setattr(r, field, value)
    hotel_owner_id = (
        await db.execute(select(Hotel.owner_user_id).where(Hotel.id == hotel_id))
    ).scalar_one()
    await db.commit()
    await db.refresh(r)
    await audit(
        db, ctx,
        owner_user_id=hotel_owner_id,
        action="room.update",
        subject_type="room",
        subject_id=r.id,
        payload={"hotel_id": hotel_id, "changed_fields": list(data.keys())},
    )
    return RoomPartnerView.from_model(r)


@router.delete("/hotels/{hotel_id}/rooms/{room_id}", status_code=204)
async def delete_room(
    hotel_id: int,
    room_id: int,
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    r = await scope.get_my_room(db, ctx, room_id, hotel_id=hotel_id, require_perm="manage_rooms")
    if await _room_has_active_bookings(db, r.id):
        raise APIError(409, "conflict", "Room has active bookings")
    hotel_owner_id = (
        await db.execute(select(Hotel.owner_user_id).where(Hotel.id == hotel_id))
    ).scalar_one()
    snapshot = {"hotel_id": hotel_id, "name_ru": r.name_ru, "capacity": r.capacity}
    room_id_snap = r.id
    await db.delete(r)
    await db.commit()
    await audit(
        db, ctx,
        owner_user_id=hotel_owner_id,
        action="room.delete",
        subject_type="room",
        subject_id=room_id_snap,
        payload=snapshot,
    )
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
    await scope.get_my_room(db, ctx, room_id, hotel_id=hotel_id)
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
    await scope.get_my_room(db, ctx, room_id, hotel_id=hotel_id, require_perm="manage_rooms")

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
    hotel_owner_id = (
        await db.execute(select(Hotel.owner_user_id).where(Hotel.id == hotel_id))
    ).scalar_one()
    await db.commit()
    if payload.nights:
        await audit(
            db, ctx,
            owner_user_id=hotel_owner_id,
            action="room.availability_update",
            subject_type="room",
            subject_id=room_id,
            payload={"nights_count": len(payload.nights), "hotel_id": hotel_id},
        )

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




# ─── /p/rooms (flat list with today_status) ────────────────────────────────

@router.get("/rooms", response_model=list[RoomFlatView])
async def list_all_my_rooms(
    owner_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    accessible_ids = scope.scope_owner_ids(ctx, owner_id)
    today = date.today()
    rows = (
        await db.execute(
            select(Room, Hotel)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .where(Hotel.owner_user_id.in_(accessible_ids))
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
            single_beds=r.single_beds,
            double_beds=r.double_beds,
            floor=r.floor,
            price_kgs=r.price_kgs,
            today_status=today_by_room.get(r.id, AvailabilityStatus.free),
            photo=photo,
        ))
    return out


