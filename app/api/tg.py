"""Telegram bot webhook handler.

One endpoint per role: /api/v1/tg/{client,partner,admin}.
Telegram is configured to POST updates here. On /start [param] we reply with
a sendMessage that has an inline-keyboard `web_app` button opening the
correct WebApp URL.
"""
import re

import httpx
from fastapi import APIRouter, Header, Request

from app.core.config import settings
from app.core.exceptions import APIError

router = APIRouter(prefix="/tg", tags=["telegram-webhook"])


_ROLE_CONFIG = {
    "client": ("tg_bot_token_client", "public_base_client", "Открыть"),
    "partner": ("tg_bot_token_partner", "public_base_partner", "Открыть кабинет"),
    "admin": ("tg_bot_token_admin", "public_base_admin", "Открыть админку"),
}


def _build_target_url(base_url: str, start_param: str) -> str:
    """Map /start param into the right WebApp URL with hash routing."""
    if not start_param:
        return base_url
    m = re.match(
        r"^hotel_(\d+)(?:_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})_(\d+))?$",
        start_param,
    )
    if not m:
        return base_url
    hid, ci, co, g = m.groups()
    qs = f"?check_in={ci}&check_out={co}&guests={g}" if ci else ""
    # base_url already ends with "/"; hash starts with "#"
    return f"{base_url}#/hotel/{hid}{qs}"


@router.post("/{role}")
async def tg_webhook(
    role: str,
    request: Request,
    x_secret: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
):
    cfg = _ROLE_CONFIG.get(role)
    if cfg is None:
        raise APIError(404, "not_found", "Unknown bot role")

    if settings.tg_webhook_secret:
        if x_secret != settings.tg_webhook_secret:
            raise APIError(403, "forbidden", "Invalid webhook secret")

    token_attr, base_attr, button_label = cfg
    bot_token = getattr(settings, token_attr, "")
    base_url = getattr(settings, base_attr, "")
    if not bot_token or not base_url:
        raise APIError(500, "config", f"Bot config missing for {role}")

    update = await request.json()
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        # Other update types — ignore, just ack.
        return {"ok": True}

    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()

    if not text.startswith("/start"):
        # Unknown command — silent ack.
        return {"ok": True}

    parts = text.split(maxsplit=1)
    start_param = parts[1].strip() if len(parts) > 1 else ""
    target_url = _build_target_url(base_url, start_param)

    reply = {
        "chat_id": chat_id,
        "text": "Нажмите кнопку, чтобы открыть приложение:",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": button_label, "web_app": {"url": target_url}}],
            ],
        },
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json=reply,
        )
        # We swallow TG errors — webhook must ack 200 either way (otherwise TG
        # will retry the same update).
        if r.status_code != 200:
            # Log to stdout for debugging; not propagated to caller.
            print(f"[tg webhook {role}] sendMessage failed: {r.status_code} {r.text}")

    return {"ok": True}
