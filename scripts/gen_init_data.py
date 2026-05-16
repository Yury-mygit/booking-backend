"""Generate a valid Telegram WebApp `initData` string for smoke-testing.

Usage:
    python scripts/gen_init_data.py <bot_token> [user_id] [first_name] [language_code]

Prints a URL-encoded string suitable for posting to /api/v1/auth/tg as init_data.
"""
import hashlib
import hmac
import json
import sys
import time
from urllib.parse import urlencode


def make_init_data(
    bot_token: str,
    user_id: int = 1001,
    first_name: str = "Test",
    language_code: str = "ru",
    auth_date: int | None = None,
) -> str:
    user = {"id": user_id, "first_name": first_name, "language_code": language_code}
    payload = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAH_test_query_id",
        "user": json.dumps(user, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(payload.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    payload["hash"] = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(payload)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    token = sys.argv[1]
    user_id = int(sys.argv[2]) if len(sys.argv) > 2 else 1001
    first_name = sys.argv[3] if len(sys.argv) > 3 else "Test"
    lang = sys.argv[4] if len(sys.argv) > 4 else "ru"
    print(make_init_data(token, user_id=user_id, first_name=first_name, language_code=lang))
