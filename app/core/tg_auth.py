"""Verify Telegram WebApp `initData` signature.

Spec: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

Transitional state (until partner/admin bots are removed): we still try all
three bot tokens so legacy fronts opened via their own bot keep authenticating.
`matched_role` is returned as a legacy default for sessions created without an
explicit `requested_role`. After bots are consolidated this collapses to a
single token.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from app.core.config import settings
from app.models.models import UserRole


class InitDataError(Exception):
    pass


def _check_signature(init_data: str, bot_token: str) -> dict | None:
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


def verify_init_data(init_data: str) -> tuple[UserRole | None, dict]:
    """Return (matched_role, telegram_user dict). Raises InitDataError on failure.

    `matched_role` reflects which bot token validated the signature — used as
    the legacy default role when the caller did not supply `requested_role`.
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
        pairs = _check_signature(init_data, token)
        if pairs is not None:
            matched = (role, pairs)
            break

    if matched is None:
        raise InitDataError("invalid signature")

    matched_role, pairs = matched

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

    return matched_role, user
