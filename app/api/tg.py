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


_BUTTON_LABEL = {"ru": "Начать", "ky": "Баштоо", "en": "Start"}
_PROMPT = {
    "ru": "Нажмите кнопку, чтобы открыть приложение:",
    "ky": "Колдонмону ачуу үчүн баскычты басыңыз:",
    "en": "Tap the button to open the app:",
}
_HOTEL_PROMPT = {
    "ru": "Бронирование отеля\n{hotel}",
    "ky": "Мейманкананы брондоо\n{hotel}",
    "en": "Hotel booking\n{hotel}",
}

# hotel_<slug> либо hotel_<slug>_<ci>_<co>_<guests>
_HOTEL_SP_RE = re.compile(r"^hotel_(.+?)(?:_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}_\d+)?$")


def _pick_lang(message: dict) -> str:
    code = ((message.get("from") or {}).get("language_code") or "").split("-")[0].lower()
    return code if code in ("ru", "ky", "en") else "ru"


def _app_url(start_param: str) -> str:
    # Юрий, 2026-05-31: убрали ?startapp=<deep-link> из кнопки бота —
    # парсинг deep-link во фронте сломался после серии правок entry/
    # fullscreen. Кнопка открывает WebApp на base URL, юзер сам
    # навигируется в отель/комнату. Восстановить deep link — отдельная
    # задача после стабилизации client SPA.
    _ = start_param  # сохраняем сигнатуру, prompt всё ещё может зависеть.
    return settings.public_base_app.rstrip("/") + "/"


def _hotel_name_by_slug(hotel: Hotel | None, lang: str) -> str | None:
    if hotel is None:
        return None
    if lang == "ky" and hotel.name_ky:
        return hotel.name_ky
    if lang == "en" and hotel.name_en:
        return hotel.name_en
    return hotel.name_ru


async def _build_prompt(db: AsyncSession, start_param: str, lang: str) -> str:
    """hotel_<slug>[...] → «Бронирование отеля\\n<имя>». Иначе — стандартный."""
    if start_param:
        m = _HOTEL_SP_RE.match(start_param)
        if m:
            slug = m.group(1)
            hotel = (
                await db.execute(select(Hotel).where(Hotel.slug == slug))
            ).scalar_one_or_none()
            name = _hotel_name_by_slug(hotel, lang)
            if name:
                return _HOTEL_PROMPT[lang].format(hotel=name)
    return _PROMPT[lang]


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
    lang = _pick_lang(msg)

    # Любой инпут (включая /start и свободный текст) — отвечаем кнопкой «Начать».
    start_param = ""
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        start_param = parts[1].strip() if len(parts) > 1 else ""

    prompt_text = await _build_prompt(db, start_param, lang)

    payload = {
        "chat_id": chat_id,
        "text": prompt_text,
        "reply_markup": {
            "inline_keyboard": [
                [{"text": _BUTTON_LABEL[lang], "web_app": {"url": _app_url(start_param)}}],
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
