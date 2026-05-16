import secrets
from datetime import date, timedelta
from typing import Iterator

BOOKING_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
BOOKING_CODE_LEN = 8


def gen_booking_code() -> str:
    return "".join(secrets.choice(BOOKING_CODE_ALPHABET) for _ in range(BOOKING_CODE_LEN))


def date_range_nights(check_in: date, check_out: date) -> Iterator[date]:
    """Yield each night between check_in (inclusive) and check_out (exclusive)."""
    d = check_in
    while d < check_out:
        yield d
        d += timedelta(days=1)
