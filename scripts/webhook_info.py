"""Show getWebhookInfo for all three bots."""
import asyncio
import json
import sys

import httpx

from app.core.config import settings


ROLES = [
    ("client", settings.tg_bot_token_client),
    ("partner", settings.tg_bot_token_partner),
    ("admin", settings.tg_bot_token_admin),
]


async def main() -> int:
    async with httpx.AsyncClient(timeout=15) as cl:
        for role, token in ROLES:
            if not token:
                print(f"{role}: no token")
                continue
            r = await cl.get(f"https://api.telegram.org/bot{token}/getWebhookInfo")
            print(f"=== {role} ===")
            print(json.dumps(r.json(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
