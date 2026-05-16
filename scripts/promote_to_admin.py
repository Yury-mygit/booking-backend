"""Promote a user to admin role.

Usage (inside container):
    docker exec booking_dev_app python scripts/promote_to_admin.py --telegram-id 123456
    docker exec booking_dev_app python scripts/promote_to_admin.py --user-id 7

If the user doesn't exist yet, --telegram-id will create a stub user with
role=admin. This is the only way to bootstrap the very first admin
(subsequent admins can be promoted via POST /api/v1/admin/users/{id}/promote-admin).
"""
import argparse
import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.models import Lang, User, UserRole


async def main(telegram_id: int | None, user_id: int | None) -> None:
    async with AsyncSessionLocal() as db:
        if user_id is not None:
            user = (
                await db.execute(select(User).where(User.id == user_id))
            ).scalar_one_or_none()
            if user is None:
                print(f"user_id={user_id} not found")
                return
        else:
            user = (
                await db.execute(select(User).where(User.telegram_id == telegram_id))
            ).scalar_one_or_none()
            if user is None:
                user = User(
                    telegram_id=telegram_id,
                    role=UserRole.admin,
                    lang=Lang.ru,
                    first_name="admin-bootstrap",
                )
                db.add(user)
                await db.commit()
                await db.refresh(user)
                print(f"created admin stub: id={user.id} tg={user.telegram_id}")
                return

        prev = user.role
        user.role = UserRole.admin
        await db.commit()
        print(
            f"promoted user.id={user.id} tg={user.telegram_id} "
            f"role: {prev.value} → admin"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--telegram-id", type=int)
    g.add_argument("--user-id", type=int)
    args = parser.parse_args()
    asyncio.run(main(args.telegram_id, args.user_id))
