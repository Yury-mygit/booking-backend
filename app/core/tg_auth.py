"""Verify Telegram WebApp `initData` signature.

Spec: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
import hashlib
import hmac
import json
import time
from typing import Literal
from urllib.parse import parse_qsl

from app.core.config import settings
from app.models.models import UserRole

Role = Literal[UserRole.client, UserRole.partner]


class InitDataError(Exception):
    pass


def _verify_with_token(init_data: str, bot_token: str) -> dict | None:
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None
    return pairs


def verify_init_data(init_data: str) -> tuple[UserRole, dict]:
    """Verify against client/partner/admin bot tokens, return (role, telegram_user dict).

    Raises InitDataError on any failure.
    """
    candidates: list[tuple[UserRole, str]] = []
    if settings.tg_bot_token_client:
        candidates.append((UserRole.client, settings.tg_bot_token_client))
    if settings.tg_bot_token_partner:
        candidates.append((UserRole.partner, settings.tg_bot_token_partner))
    if settings.tg_bot_token_admin:
        candidates.append((UserRole.admin, settings.tg_bot_token_admin))

    matched: tuple[UserRole, dict] | None = None
    for role, token in candidates:
        pairs = _verify_with_token(init_data, token)
        if pairs is not None:
            matched = (role, pairs)
            break

    if matched is None:
        raise InitDataError("invalid signature")

    role, pairs = matched

    auth_date_raw = pairs.get("auth_date")
    if not auth_date_raw:
        raise InitDataError("missing auth_date")
    try:
        auth_date = int(auth_date_raw)
    except ValueError as exc:
        raise InitDataError("invalid auth_date") from exc
    age = int(time.time()) - auth_date
    if age > settings.tg_init_data_max_age_sec:
        raise InitDataError("init_data expired")
    if age < -60:
        raise InitDataError("auth_date in the future")

    user_raw = pairs.get("user")
    if not user_raw:
        raise InitDataError("missing user")
    try:
        user = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise InitDataError("invalid user json") from exc
    if not isinstance(user.get("id"), int):
        raise InitDataError("invalid user.id")

    return role, user
