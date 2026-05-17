import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

BOT_TOKEN = "12345:test-bot-token"


def sign_init_data(
    user: dict,
    bot_token: str = BOT_TOKEN,
    auth_date: int | None = None,
    extra: dict | None = None,
) -> str:
    """Form a URL-encoded initData string with a valid HMAC, matching Telegram's algorithm.

    Used by tests to produce known-good and (via mutation) known-bad inputs.
    """
    if auth_date is None:
        auth_date = int(time.time())
    fields: dict[str, str] = {
        "auth_date": str(auth_date),
        "user": json.dumps(user, separators=(",", ":")),
    }
    if extra:
        fields.update({k: str(v) for k, v in extra.items()})

    data_check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    return urlencode({**fields, "hash": h})


@pytest.fixture
def bot_token() -> str:
    return BOT_TOKEN


@pytest.fixture
def valid_user() -> dict:
    return {
        "id": 12345,
        "first_name": "Orrin",
        "last_name": "Test",
        "username": "orrin_test",
        "language_code": "en",
        "is_premium": True,
    }


@pytest.fixture
def valid_init_data(valid_user: dict) -> str:
    return sign_init_data(valid_user)
