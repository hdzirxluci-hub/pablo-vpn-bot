import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from app.auth import InitDataError, validate_init_data


def signed_init_data(token: str, auth_date: int, user_id: int = 842103) -> str:
    values = {
        "auth_date": str(auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps({"id": user_id, "first_name": "Ali", "username": "ali_demo"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_valid_telegram_init_data() -> None:
    token = "123456:TEST_TOKEN"
    raw = signed_init_data(token, auth_date=1_800_000_000)
    identity = validate_init_data(raw, token, max_age_seconds=3600, now=1_800_000_100)
    assert identity.id == 842103
    assert identity.username == "ali_demo"


def test_invalid_signature_is_rejected() -> None:
    raw = signed_init_data("correct-token", auth_date=1_800_000_000)
    with pytest.raises(InitDataError, match="signature"):
        validate_init_data(raw, "wrong-token", max_age_seconds=3600, now=1_800_000_100)


def test_expired_init_data_is_rejected() -> None:
    token = "123456:TEST_TOKEN"
    raw = signed_init_data(token, auth_date=1_700_000_000)
    with pytest.raises(InitDataError, match="expired"):
        validate_init_data(raw, token, max_age_seconds=60, now=1_800_000_000)
