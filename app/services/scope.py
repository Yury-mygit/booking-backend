"""Owner-scope DB helpers для partner-роутов.

Все функции работают в скоупе `ctx.accessible_owners` — собственные
hotels плюс staff-membership. Опциональный `require_perm` проверяет
один из 4 staff-флагов (`manage_hotel/rooms/bookings/staff`) поверх
scope'а; у owner'а флаги всегда true.

Raises:
    APIError(404, "not_found", ...) — объект не найден или вне scope'а.
    APIError(403, "permission_denied", ...) — scope ок, но `require_perm`
        не выполнен.

Используется из `api/partner.py` и `api/uploads.py`. До 2026-05-26 эти
helpers жили локально в каждом файле и расходились: uploads-версия
проверяла только `owner_user_id == ctx.user.id`, отрезая staff'у
доступ к фото. Унифицировано на `accessible_owners` — теперь staff с
правом на отель видит фото отеля.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.core.exceptions import APIError
from app.models.models import Booking, Client, Hotel, HotelService, Room


def scope_owner_ids(ctx: AuthContext, owner_id: int | None = None) -> list[int]:
    """owner_user_id set для scope-aware list-запросов.

    owner_id=None — все accessible owners.
    owner_id задан — должен быть в accessible_owners, иначе 404.
    """
    if owner_id is None:
        return list(ctx.accessible_owners.keys())
    if owner_id not in ctx.accessible_owners:
        raise APIError(404, "not_found", "Owner not accessible")
    return [owner_id]


def _check_perm(
    ctx: AuthContext,
    owner_user_id: int,
    hotel_id: int | None,
    require_perm: str,
) -> None:
    """Scoped permission check. `hotel_id=None` ⇒ action не привязан к
    отелю (e.g. /p/staff CRUD) — учитываются только override и NULL-scope
    (global) роли."""
    op = ctx.accessible_owners[owner_user_id]
    if not op.can(hotel_id, require_perm):
        raise APIError(403, "permission_denied", f"Missing permission: {require_perm}")


async def get_my_hotel(
    db: AsyncSession,
    ctx: AuthContext,
    hotel_id: int,
    *,
    require_perm: str | None = None,
) -> Hotel:
    hotel = (
        await db.execute(
            select(Hotel).where(
                Hotel.id == hotel_id,
                Hotel.owner_user_id.in_(scope_owner_ids(ctx)),
            )
        )
    ).scalar_one_or_none()
    if hotel is None:
        raise APIError(404, "not_found", "Hotel not found")
    if require_perm is not None:
        _check_perm(ctx, hotel.owner_user_id, hotel.id, require_perm)
    return hotel


async def get_my_room(
    db: AsyncSession,
    ctx: AuthContext,
    room_id: int,
    *,
    hotel_id: int | None = None,
    require_perm: str | None = None,
) -> Room:
    """Найти Room в scope'е.

    Если задан `hotel_id` — фильтруем Room.hotel_id == hotel_id (используется
    в /p/hotels/{hotel_id}/rooms/{room_id}/* endpoints).
    Если `hotel_id is None` — ищем room по id, ownership проверяем через JOIN
    (используется в /p/rooms/{room_id}/photos).
    """
    if hotel_id is not None:
        hotel = await get_my_hotel(db, ctx, hotel_id, require_perm=require_perm)
        room = (
            await db.execute(
                select(Room).where(Room.id == room_id, Room.hotel_id == hotel.id)
            )
        ).scalar_one_or_none()
        if room is None:
            raise APIError(404, "not_found", "Room not found")
        return room

    row = (
        await db.execute(
            select(Room, Hotel)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .where(Room.id == room_id, Hotel.owner_user_id.in_(scope_owner_ids(ctx)))
        )
    ).first()
    if row is None:
        raise APIError(404, "not_found", "Room not found")
    room, hotel = row
    if require_perm is not None:
        _check_perm(ctx, hotel.owner_user_id, hotel.id, require_perm)
    return room


async def get_my_service(
    db: AsyncSession,
    ctx: AuthContext,
    hotel_id: int,
    service_id: int,
    *,
    require_perm: str | None = None,
) -> HotelService:
    await get_my_hotel(db, ctx, hotel_id, require_perm=require_perm)
    svc = (
        await db.execute(
            select(HotelService).where(
                HotelService.id == service_id,
                HotelService.hotel_id == hotel_id,
            )
        )
    ).scalar_one_or_none()
    if svc is None:
        raise APIError(404, "not_found", "Service not found")
    return svc


async def get_my_booking(
    db: AsyncSession,
    ctx: AuthContext,
    code: str,
    *,
    require_perm: str | None = None,
) -> tuple[Booking, Room, Hotel, Client]:
    row = (
        await db.execute(
            select(Booking, Room, Hotel, Client)
            .join(Room, Room.id == Booking.room_id)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .join(Client, Client.id == Booking.client_id)
            .where(Booking.code == code, Hotel.owner_user_id.in_(scope_owner_ids(ctx)))
            .with_for_update(of=Booking)
        )
    ).first()
    if row is None:
        raise APIError(404, "not_found", "Booking not found")
    if require_perm is not None:
        _check_perm(ctx, row[2].owner_user_id, row[2].id, require_perm)
    return row


async def get_my_client(
    db: AsyncSession,
    ctx: AuthContext,
    client_id: int,
) -> Client:
    """Client виден partner-scope юзеру только если у него есть бронь
    в одном из отелей accessible_owners."""
    c = (
        await db.execute(
            select(Client)
            .join(Booking, Booking.client_id == Client.id)
            .join(Room, Room.id == Booking.room_id)
            .join(Hotel, Hotel.id == Room.hotel_id)
            .where(Client.id == client_id, Hotel.owner_user_id.in_(scope_owner_ids(ctx)))
            .limit(1)
        )
    ).scalar_one_or_none()
    if c is None:
        raise APIError(404, "not_found", "Client not found")
    return c
