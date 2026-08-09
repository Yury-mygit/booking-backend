"""HMAC-signature validation для inbound webhooks (DevPay etc.).

Формат заголовков (DP-5):
- `X-Devpay-Signature: sha256=<hex>` — HMAC-SHA256(secret, raw_body).
- `X-Devpay-Timestamp: <unix-seconds>` — skew ≤300s.
- `X-Devpay-Idempotency-Key: <intent_id>` — event id для caching.
"""
from __future__ import annotations

import hashlib
import hmac
import time


MAX_SKEW_SEC = 300


def compute_signature(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, signature_header: str | None, secret: str) -> bool:
    if not signature_header:
        return False
    expected = compute_signature(body, secret)
    return hmac.compare_digest(expected, signature_header)


def verify_timestamp(timestamp_header: str | None) -> bool:
    if not timestamp_header:
        return False
    try:
        ts = int(timestamp_header)
    except ValueError:
        return False
    now = int(time.time())
    return abs(now - ts) <= MAX_SKEW_SEC
