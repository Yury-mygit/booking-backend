"""TG Bot API primitives (outbound).

Общий transport-helper поверх `POST /bot<token>/sendMessage`.
Используется notification-механизмами (`services/tg_notifications`) и
partner-action endpoints (share-to-self и т.п.).

Не путать с `api/tg.py` — тот принимает webhook'и от TG (inbound).
"""
import httpx

from app.core.config import settings
from app.models.models import Hotel


def hotel_name_by_lang(hotel: Hotel, lang: str) -> str:
    """Локализованное имя отеля с fallback на name_ru."""
    if lang == "ky" and hotel.name_ky:
        return hotel.name_ky
    if lang == "en" and hotel.name_en:
        return hotel.name_en
    return hotel.name_ru


async def send_button_message(
    chat_id: int,
    text: str,
    deep_link: str,
    btn_label: str,
) -> int:
    """Отправить сообщение с одной inline-кнопкой `web_app`.

    Returns TG HTTP status (200 ok, 400 chat not found / /start не нажат,
    403 bot blocked, 0 при transport-error). Caller решает, что делать со
    статусом (dedupe, флаг `bot_blocked_or_unreachable`, retry).
    """
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [
                [{"text": btn_label, "web_app": {"url": deep_link}}],
            ],
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{settings.tg_bot_token}/sendMessage",
                json=payload,
            )
        return r.status_code
    except httpx.HTTPError as e:
        print(f"[tg_bot] send error: {e}")
        return 0
