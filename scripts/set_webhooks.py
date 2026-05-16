"""Set Telegram webhooks for all three bots.

Run inside container:
    docker exec booking_dev_app python scripts/set_webhooks.py

Each bot's webhook is set to the *same* domain (book.dev.raftforge.art),
since one backend handles all three. The path encodes the role.

Reads bot tokens and TG_WEBHOOK_SECRET from settings (.env).
"""
import asyncio
import sys

import httpx

from app.core.config import settings


WEBHOOK_BASE = "https://book.dev.raftforge.art/api/v1/tg"

ROLES = [
    ("client", settings.tg_bot_token_client),
    ("partner", settings.tg_bot_token_partner),
    ("admin", settings.tg_bot_token_admin),
]


async def main() -> int:
    if not settings.tg_webhook_secret:
        print("ERROR: TG_WEBHOOK_SECRET is empty — set it in .env first", file=sys.stderr)
        return 2

    async with httpx.AsyncClient(timeout=15) as cl:
        for role, token in ROLES:
            if not token:
                print(f"{role}: token missing, skipped")
                continue
            url = f"{WEBHOOK_BASE}/{role}"
            r = await cl.post(
                f"https://api.telegram.org/bot{token}/setWebhook",
                json={
                    "url": url,
                    "secret_token": settings.tg_webhook_secret,
                    "drop_pending_updates": True,
                    "allowed_updates": ["message"],
                },
            )
            data = r.json()
            ok = data.get("ok")
            print(f"{role:8s} → {url}  →  {r.status_code} {data.get('description', data)}")
            if not ok:
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
