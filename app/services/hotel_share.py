"""Share-to-self: партнёр отправляет себе в TG deep-link на отель.

Bridge между partner endpoint (`api/partner/hotels.py`) и TG transport
(`services/tg_bot.send_button_message`). Держит здесь i18n-шаблоны + логику
по `User.bot_blocked_or_unreachable` flag'у, чтобы route-файл остался тонким.
"""
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import Hotel, User
from app.services.tg_bot import hotel_name_by_lang, send_button_message


ShareOutcome = str  # "ok" | "bot_not_started" | "upstream_error" | "not_configured"


_TPL_SHARE_HOTEL = {
    "ru": "Отель «{hotel}»",
    "ky": "Мейманкана «{hotel}»",
    "en": "Hotel «{hotel}»",
}
_BTN_OPEN = {"ru": "Открыть", "ky": "Ачуу", "en": "Open"}


def _lang_str(user_lang) -> str:
    s = user_lang.value if hasattr(user_lang, "value") else str(user_lang)
    return s if s in ("ru", "ky", "en") else "ru"


def _deep_link_hotel(slug: str) -> str:
    base = settings.public_base_app.rstrip("/") + "/"
    return f"{base}?startapp=hotel_{slug}"


async def _set_blocked(db: AsyncSession, user_id: int, blocked: bool) -> None:
    await db.execute(
        update(User)
        .where(User.id == user_id, User.bot_blocked_or_unreachable != blocked)
        .values(bot_blocked_or_unreachable=blocked)
    )
    await db.commit()


async def share_hotel_to_self(
    db: AsyncSession,
    user: User,
    hotel: Hotel,
) -> ShareOutcome:
    """Отправить `user`'у в личку от бота карточку `hotel` с startapp deep-link.

    - Precheck `bot_blocked_or_unreachable` → `bot_not_started`.
    - 200 → sync flag=False, `ok`.
    - 400/403 → sync flag=True, `bot_not_started`.
    - transport/5xx → `upstream_error` (флаг не двигаем, транзиент).
    - Bot token не сконфигурен → `not_configured` (5xx на уровне endpoint'а).
    """
    if not settings.tg_bot_token:
        return "not_configured"
    if user.bot_blocked_or_unreachable:
        return "bot_not_started"

    lang = _lang_str(user.lang)
    text = _TPL_SHARE_HOTEL[lang].format(hotel=hotel_name_by_lang(hotel, lang))
    deep_link = _deep_link_hotel(hotel.slug)

    status = await send_button_message(user.telegram_id, text, deep_link, _BTN_OPEN[lang])

    if status == 200:
        if user.bot_blocked_or_unreachable:
            await _set_blocked(db, user.id, False)
        return "ok"
    if status in (400, 403):
        await _set_blocked(db, user.id, True)
        return "bot_not_started"
    return "upstream_error"
