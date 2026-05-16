"""Seed minimal data for Этап 4 smoke: 1 partner-user + 1 published hotel + 2 rooms + 1 blocked night.

Run inside container:
    docker exec booking_dev_app python scripts/seed_demo.py
"""
import asyncio
from datetime import date, timedelta

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.models import (
    Availability,
    AvailabilityStatus,
    Hotel,
    HotelStatus,
    Lang,
    Room,
    User,
    UserRole,
)


async def main() -> None:
    async with AsyncSessionLocal() as db:
        partner = (
            await db.execute(select(User).where(User.telegram_id == 999001))
        ).scalar_one_or_none()
        if partner is None:
            partner = User(
                telegram_id=999001,
                role=UserRole.partner,
                lang=Lang.ru,
                first_name="DemoPartner",
            )
            db.add(partner)
            await db.flush()

        hotel = (
            await db.execute(select(Hotel).where(Hotel.name_ru == "Демо-отель"))
        ).scalar_one_or_none()
        if hotel is None:
            hotel = Hotel(
                owner_user_id=partner.id,
                name_ru="Демо-отель",
                name_ky="Демо-мейманкана",
                name_en="Demo Hotel",
                description_ru="Тестовый отель для smoke",
                city="Bishkek",
                address="ул. Чуй 1",
                lat=42.876,
                lng=74.604,
                photos=["https://example.com/demo.jpg"],
                status=HotelStatus.published,
            )
            db.add(hotel)
            await db.flush()

            db.add_all(
                [
                    Room(
                        hotel_id=hotel.id,
                        name_ru="Стандарт",
                        name_en="Standard",
                        capacity=2,
                        price_kgs=2500,
                        photos=[],
                    ),
                    Room(
                        hotel_id=hotel.id,
                        name_ru="Люкс",
                        name_en="Suite",
                        capacity=4,
                        price_kgs=5500,
                        photos=[],
                    ),
                ]
            )
            await db.flush()

        rooms = (
            (await db.execute(select(Room).where(Room.hotel_id == hotel.id))).scalars().all()
        )
        # Block one night on the first room (tomorrow) to test conflict path.
        tomorrow = date.today() + timedelta(days=1)
        existing_block = (
            await db.execute(
                select(Availability).where(
                    Availability.room_id == rooms[0].id, Availability.date == tomorrow
                )
            )
        ).scalar_one_or_none()
        if existing_block is None:
            db.add(
                Availability(
                    room_id=rooms[0].id,
                    date=tomorrow,
                    status=AvailabilityStatus.blocked,
                )
            )

        await db.commit()
        print(f"partner.id={partner.id}, hotel.id={hotel.id}, rooms={[r.id for r in rooms]}")


if __name__ == "__main__":
    asyncio.run(main())
