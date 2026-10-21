from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException, status

from .config import get_settings
from .schemas import TelegramIdentity


class InitDataError(ValueError):
    pass


def validate_init_data(
    init_data: str, bot_token: str, max_age_seconds: int = 3600, now: int | None = None
) -> TelegramIdentity:
    if not init_data:
        raise InitDataError("Telegram initData is empty")

    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", None)
    if not received_hash:
        raise InitDataError("Telegram initData hash is missing")

    auth_date_raw = values.get("auth_date")
    if not auth_date_raw:
        raise InitDataError("Telegram initData auth_date is missing")
    try:
        auth_date = int(auth_date_raw)
    except ValueError as exc:
        raise InitDataError("Telegram initData auth_date is invalid") from exc

    current_time = int(time.time()) if now is None else now
    if auth_date > current_time + 30:
        raise InitDataError("Telegram initData is from the future")
    if current_time - auth_date > max_age_seconds:
        raise InitDataError("Telegram initData has expired")

    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise InitDataError("Telegram initData signature is invalid")

    raw_user = values.get("user")
    if not raw_user:
        raise InitDataError("Telegram initData user is missing")
    try:
        user_data = json.loads(raw_user)
        return TelegramIdentity.model_validate(user_data)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise InitDataError("Telegram user data is invalid") from exc


async def current_identity(
    authorization: str | None = Header(default=None),
    x_demo_user_id: str | None = Header(default=None),
    x_demo_owner: str | None = Header(default=None),
) -> TelegramIdentity:
    settings = get_settings()
    if authorization and authorization.lower().startswith("tma "):
        raw_init_data = authorization[4:].strip()
        if settings.demo_mode and raw_init_data == "signed-demo-init-data":
            return TelegramIdentity(
                id=842103,
                first_name="علی",
                last_name="محمدی",
                username="ali_demo",
                language_code="fa",
            )
        if not settings.is_bot_configured:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bot token is not configured")
        try:
            return validate_init_data(
                raw_init_data,
                settings.bot_token.get_secret_value(),
                settings.init_data_max_age_seconds,
            )
        except InitDataError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    if settings.demo_mode:
        try:
            telegram_id = int(
                x_demo_user_id
                or (settings.owner_telegram_id if x_demo_owner == "1" and settings.owner_telegram_id else 842103)
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid demo user id") from exc
        is_owner = bool(settings.owner_telegram_id and telegram_id == settings.owner_telegram_id)
        return TelegramIdentity(
            id=telegram_id,
            first_name="Pablo Owner" if is_owner else "علی",
            last_name=None if is_owner else "محمدی",
            username="pablo_owner" if is_owner else "ali_demo",
            language_code="fa",
        )

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Telegram authentication is required")
