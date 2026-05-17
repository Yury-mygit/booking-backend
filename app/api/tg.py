"""Telegram bot webhook handler.

One endpoint per role: /api/v1/tg/{client,partner,admin}.
Telegram POSTs updates here.

Client bot:
- /start hotel_<slug>[_<ci>_<co>_<g>] — sendPhoto card (name + Подробнее → WebApp).
- /start (no param) — ask user to type hotel name.
- free text (not /command) — ILIKE search by name; hit → card, miss → not-found.
- /start hotel_<unknown> or malformed param — not-found.

Partner/admin bots:
- /start [param] — plain prompt with web_app button (no card flow).
"""
import html
import re

import httpx
from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import APIError
from app.models.models import Hotel, HotelStatus

router = APIRouter(prefix="/tg", tags=["telegram-webhook"])


_ROLE_CONFIG = {
    "client": ("tg_bot_token_client", "public_base_client", "Открыть"),
    "partner": ("tg_bot_token_partner", "public_base_partner", "Открыть кабинет"),
    "admin": ("tg_bot_token_admin", "public_base_admin", "Открыть админку"),
}

_DETAILS_LABEL = {"ru": "Подробнее", "ky": "Толугураак", "en": "View details"}
_FALLBACK_PROMPT = {
    "ru": "Нажмите кнопку, чтобы открыть приложение:",
    "ky": "Колдонмону ачуу үчүн баскычты басыңыз:",
    "en": "Tap the button to open the app:",
}
_ASK_HOTEL_NAME = {
    "ru": "Введите название отеля, чтобы посмотреть карточку:",
    "ky": "Мейманкананын аталышын жазыңыз:",
    "en": "Type a hotel name to see its card:",
}
_HOTEL_NOT_FOUND = {
    "ru": "Отель не найден. Попробуйте ввести другое название.",
    "ky": "Мейманкана табылган жок. Башка аталыш жазып көрүңүз.",
    "en": "Hotel not found. Try a different name.",
}


def _pick_lang(message: dict) -> str:
    code = ((message.get("from") or {}).get("language_code") or "").split("-")[0].lower()
    return code if code in ("ru", "ky", "en") else "ru"


def _localized(hotel: Hotel, field: str, lang: str) -> str | None:
    """Return hotel.<field>_<lang> with fallback ru → en."""
    for cand in (lang, "ru", "en"):
        v = getattr(hotel, f"{field}_{cand}", None)
        if v:
            return v
    return None


def _absolutize(url: str, base: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return base.rstrip("/") + "/" + url.lstrip("/")


def _build_target_url(base_url: str, start_param: str) -> str:
    """Map /start param into the right WebApp URL.

    Identifier is a slug (a-z0-9-) or numeric id. Optional dates appended via _.
    NB: query string, not hash (Telegram mobile overwrites location.hash).
    """
    if not start_param:
        return base_url
    m = re.match(
        r"^hotel_(.+)_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})_(\d+)$",
        start_param,
    )
    if m:
        slug, ci, co, g = m.groups()
        return f"{base_url}?hotel={slug}&check_in={ci}&check_out={co}&guests={g}"
    m = re.match(r"^hotel_(.+)$", start_param)
    if not m:
        return base_url
    slug = m.group(1)
    return f"{base_url}?hotel={slug}"


def _extract_hotel_identifier(start_param: str) -> str | None:
    """Pull slug-or-id out of `hotel_<id>[_<ci>_<co>_<g>]`."""
    m = re.match(
        r"^hotel_(.+?)(?:_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}_\d+)?$",
        start_param,
    )
    return m.group(1) if m else None


async def _load_published_hotel(db: AsyncSession, identifier: str) -> Hotel | None:
    if identifier.isdigit():
        cond = or_(Hotel.id == int(identifier), Hotel.slug == identifier)
    else:
        cond = Hotel.slug == identifier
    row = await db.execute(
        select(Hotel).where(cond, Hotel.status == HotelStatus.published)
    )
    return row.scalar_one_or_none()


async def _find_hotel_by_name(db: AsyncSession, query: str) -> Hotel | None:
    """ILIKE по name_ru/name_ky/name_en среди published. Точное совпадение в приоритете."""
    q = query.strip()
    if not q:
        return None
    like = f"%{q}%"
    row = await db.execute(
        select(Hotel)
        .where(
            Hotel.status == HotelStatus.published,
            or_(
                Hotel.name_ru.ilike(like),
                Hotel.name_ky.ilike(like),
                Hotel.name_en.ilike(like),
            ),
        )
        .limit(1)
    )
    return row.scalar_one_or_none()


def _build_hotel_card(
    hotel: Hotel,
    lang: str,
    target_url: str,
    base_url: str,
) -> dict:
    name = _localized(hotel, "name", lang) or f"#{hotel.id}"
    caption = f"<b>{html.escape(name)}</b>"

    keyboard = {
        "inline_keyboard": [
            [{"text": _DETAILS_LABEL[lang], "web_app": {"url": target_url}}],
        ],
    }

    photos = hotel.photos or []
    if photos:
        return {
            "method": "sendPhoto",
            "payload": {
                "photo": _absolutize(photos[0], base_url),
                "caption": caption,
                "parse_mode": "HTML",
                "reply_markup": keyboard,
            },
        }
    return {
        "method": "sendMessage",
        "payload": {
            "text": caption,
            "parse_mode": "HTML",
            "reply_markup": keyboard,
            "disable_web_page_preview": True,
        },
    }


@router.post("/{role}")
async def tg_webhook(
    role: str,
    request: Request,
    x_secret: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
    db: AsyncSession = Depends(get_db),
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
        return {"ok": True}

    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    lang = _pick_lang(msg)

    if not text:
        return {"ok": True}

    is_start = text.startswith("/start")
    start_param = ""
    if is_start:
        parts = text.split(maxsplit=1)
        start_param = parts[1].strip() if len(parts) > 1 else ""

    request_spec: dict | None = None

    if role == "client":
        if is_start and start_param:
            identifier = _extract_hotel_identifier(start_param)
            hotel = await _load_published_hotel(db, identifier) if identifier else None
            if hotel is not None:
                target_url = _build_target_url(base_url, start_param)
                request_spec = _build_hotel_card(hotel, lang, target_url, base_url)
            else:
                request_spec = {
                    "method": "sendMessage",
                    "payload": {"text": _HOTEL_NOT_FOUND[lang]},
                }
        elif is_start:
            request_spec = {
                "method": "sendMessage",
                "payload": {"text": _ASK_HOTEL_NAME[lang]},
            }
        elif not text.startswith("/"):
            hotel = await _find_hotel_by_name(db, text)
            if hotel is not None:
                target_url = _build_target_url(base_url, f"hotel_{hotel.slug}")
                request_spec = _build_hotel_card(hotel, lang, target_url, base_url)
            else:
                request_spec = {
                    "method": "sendMessage",
                    "payload": {"text": _HOTEL_NOT_FOUND[lang]},
                }
        else:
            return {"ok": True}
    else:
        if not is_start:
            return {"ok": True}
        target_url = _build_target_url(base_url, start_param)
        request_spec = {
            "method": "sendMessage",
            "payload": {
                "text": _FALLBACK_PROMPT[lang],
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": button_label, "web_app": {"url": target_url}}],
                    ],
                },
            },
        }

    if request_spec is None:
        return {"ok": True}

    payload = {"chat_id": chat_id, **request_spec["payload"]}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"https://api.telegram.org/bot{bot_token}/{request_spec['method']}",
            json=payload,
        )
        if r.status_code != 200:
            # Webhook must ack 200 regardless — TG retries otherwise.
            print(f"[tg webhook {role}] {request_spec['method']} failed: {r.status_code} {r.text}")

    return {"ok": True}
