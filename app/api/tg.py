"""Telegram bot webhook handler.

Single endpoint /api/v1/tg/bot for @rforge_stay_bot. Бот тупо прокидывает
start_param в hub-WebApp; hub разруливает (роли / hotel_*-deep-link / invite_*-deep-link).

Старые `/tg/{client,partner,admin}` endpoint'ы удалены (Этап 4, см.
history/2026-05-21-booking-single-bot-hub.md). Search-by-name flow клиентского
бота тоже снят — пользователь ищет отель в самом WebApp.
"""
import re

import httpx
from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import APIError
from app.models.models import Hotel

router = APIRouter(prefix="/tg", tags=["telegram-webhook"])


_BUTTON_LABEL = "Начать"
_PROMPT = "Нажмите кнопку, чтобы открыть приложение:"
_HOTEL_PROMPT = "Бронирование отеля\n{hotel}"

# hotel_<slug> либо hotel_<slug>_<ci>_<co>_<adults>[_<children>[_<infants>]]
# (исторически было `_<guests>` — одно число; после #125 — 1-3 числа)
_HOTEL_SP_RE = re.compile(
    r"^hotel_(.+?)(?:_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}(?:_\d+)+)?$"
)


def _app_url(start_param: str) -> str:
    base = settings.public_base_app.rstrip("/") + "/"
    if start_param:
        # WebApp URL — query string, не hash (Telegram mobile перетирает hash).
        return f"{base}?startapp={start_param}"
    return base


async def _build_prompt(db: AsyncSession, start_param: str) -> str:
    """hotel_<slug>[...] → «Бронирование отеля\\n<имя>». Иначе — стандартный."""
    if start_param:
        m = _HOTEL_SP_RE.match(start_param)
        if m:
            slug = m.group(1)
            hotel = (
                await db.execute(select(Hotel).where(Hotel.slug == slug))
            ).scalar_one_or_none()
            if hotel is not None:
                return _HOTEL_PROMPT.format(hotel=hotel.name_ru)
    return _PROMPT


@router.post("/bot")
async def tg_webhook(
    request: Request,
    x_secret: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
    db: AsyncSession = Depends(get_db),
):
    if settings.tg_webhook_secret:
        if x_secret != settings.tg_webhook_secret:
            raise APIError(403, "forbidden", "Invalid webhook secret")

    if not settings.tg_bot_token:
        raise APIError(500, "config", "TG_BOT_TOKEN is empty")

    update = await request.json()
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return {"ok": True}

    text = (msg.get("text") or "").strip()
    if not text:
        return {"ok": True}

    chat_id = msg.get("chat", {}).get("id")

    # Любой инпут (включая /start и свободный текст) — отвечаем кнопкой «Начать».
    start_param = ""
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        start_param = parts[1].strip() if len(parts) > 1 else ""

    prompt_text = await _build_prompt(db, start_param)

    payload = {
        "chat_id": chat_id,
        "text": prompt_text,
        "reply_markup": {
            "inline_keyboard": [
                [{"text": _BUTTON_LABEL, "web_app": {"url": _app_url(start_param)}}],
            ],
        },
    }

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"https://api.telegram.org/bot{settings.tg_bot_token}/sendMessage",
            json=payload,
        )
        if r.status_code != 200:
            # Webhook должен ack'ать 200 — иначе TG будет ретраить.
            print(f"[tg webhook] sendMessage failed: {r.status_code} {r.text}")

    return {"ok": True}
