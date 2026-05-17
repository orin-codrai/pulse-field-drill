"""Telegram Mini App initData validation.

Реализует алгоритм из https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
Цель: при переезде в Pulse скопировать этот файл дословно — никаких сторонних либ.
"""

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from app.schemas.user import TelegramUser


class InvalidInitData(Exception):
    """Raised when initData fails any validation step."""


@dataclass(frozen=True)
class InitData:
    user: TelegramUser
    auth_date: int


def validate_init_data(raw: str, bot_token: str, max_age: int = 86400) -> InitData:
    # keep_blank_values=True важно: Telegram включает поля вроде start_param=
    # в data_check_string даже если значение пустое; без флага hash не сойдётся.
    parsed = dict(parse_qsl(raw, keep_blank_values=True))

    given_hash = parsed.pop("hash", None)
    if not given_hash:
        raise InvalidInitData("missing hash")

    # signature (Ed25519, для third-party валидации) и другие поля остаются здесь.
    data_check_string = "\n".join(f"{k}={parsed[k]}" for k in sorted(parsed))

    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, given_hash):
        raise InvalidInitData("bad signature")

    auth_date_raw = parsed.get("auth_date")
    if not auth_date_raw:
        raise InvalidInitData("missing auth_date")
    try:
        auth_date = int(auth_date_raw)
    except ValueError as e:
        raise InvalidInitData("bad auth_date") from e
    if time.time() - auth_date > max_age:
        raise InvalidInitData("stale")

    user_raw = parsed.get("user")
    if not user_raw:
        raise InvalidInitData("missing user")
    try:
        user = TelegramUser.model_validate(json.loads(user_raw))
    except (json.JSONDecodeError, ValueError) as e:
        raise InvalidInitData(f"bad user payload: {e}") from e

    return InitData(user=user, auth_date=auth_date)
