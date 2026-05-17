import json
import time
from urllib.parse import parse_qsl, urlencode

import pytest

from app.auth.init_data import InvalidInitData, validate_init_data
from tests.conftest import BOT_TOKEN, sign_init_data


def test_happy_path_returns_parsed_user(valid_init_data: str):
    result = validate_init_data(valid_init_data, BOT_TOKEN)
    assert result.user.id == 12345
    assert result.user.first_name == "Orrin"
    assert result.user.is_premium is True


def test_bad_hash_rejected(valid_init_data: str):
    # Mutate user payload after the hash was computed → signature mismatch.
    pairs = dict(parse_qsl(valid_init_data, keep_blank_values=True))
    pairs["user"] = pairs["user"].replace("Orrin", "Mallory")
    tampered = urlencode(pairs)
    with pytest.raises(InvalidInitData, match="bad signature"):
        validate_init_data(tampered, BOT_TOKEN)


def test_wrong_bot_token_rejected(valid_init_data: str):
    with pytest.raises(InvalidInitData, match="bad signature"):
        validate_init_data(valid_init_data, "different:token")


def test_stale_auth_date_rejected(valid_user: dict):
    raw = sign_init_data(valid_user, auth_date=int(time.time()) - 86401)
    with pytest.raises(InvalidInitData, match="stale"):
        validate_init_data(raw, BOT_TOKEN, max_age=86400)


def test_missing_hash_rejected(valid_init_data: str):
    pairs = dict(parse_qsl(valid_init_data, keep_blank_values=True))
    pairs.pop("hash")
    raw = urlencode(pairs)
    with pytest.raises(InvalidInitData, match="missing hash"):
        validate_init_data(raw, BOT_TOKEN)


def test_missing_user_rejected():
    # Sign initData that contains no `user` field at all.
    import hashlib
    import hmac
    auth_date = str(int(time.time()))
    fields = {"auth_date": auth_date}
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    raw = urlencode({**fields, "hash": h})
    with pytest.raises(InvalidInitData, match="missing user"):
        validate_init_data(raw, BOT_TOKEN)


def test_blank_value_field_preserved(valid_user: dict):
    # Empty `start_param=` must participate in data_check_string; if our parser
    # dropped blanks, hash wouldn't match. The signer produces a correct hash,
    # so the validator must agree.
    raw = sign_init_data(valid_user, extra={"start_param": ""})
    result = validate_init_data(raw, BOT_TOKEN)
    assert result.user.id == 12345


def test_user_parsed_only_after_hash_verified(valid_user: dict):
    # If user JSON is malformed but hash is computed over that exact malformed
    # string, validator should still fail at JSON parse step — not before hash.
    # (Sanity: malformed JSON → fail at user parse, not earlier.)
    raw = sign_init_data({"id": 1, "first_name": "x"})
    # Replace the user value with malformed JSON and re-sign so hash matches.
    pairs = dict(parse_qsl(raw, keep_blank_values=True))
    pairs["user"] = "not-json"
    pairs.pop("hash")
    dcs = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    import hashlib
    import hmac
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    pairs["hash"] = h
    bad_user_raw = urlencode(pairs)
    with pytest.raises(InvalidInitData, match="bad user payload"):
        validate_init_data(bad_user_raw, BOT_TOKEN)


def test_extra_unknown_fields_ignored(valid_user: dict):
    user_with_extras = {**valid_user, "future_field_telegram_adds": "ignored"}
    raw = sign_init_data(user_with_extras)
    result = validate_init_data(raw, BOT_TOKEN)
    assert result.user.id == 12345
