from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import delete, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    AuditLog,
    Order,
    PanelInstance,
    Plan,
    Service,
    SystemSetting,
    TrialClaim,
    TrialPolicy,
    User,
)

BACKUP_MAGIC = "PABLO-VPN-BACKUP"
BACKUP_VERSION = 1
AAD = b"PABLO VPN encrypted backup v1"
MAX_BACKUP_BYTES = 20 * 1024 * 1024

TABLE_MODELS = [
    User,
    PanelInstance,
    Plan,
    Order,
    Service,
    TrialPolicy,
    TrialClaim,
    SystemSetting,
    AuditLog,
]
RESTORE_DELETE_ORDER = [
    TrialClaim,
    AuditLog,
    Service,
    Order,
    TrialPolicy,
    Plan,
    PanelInstance,
    SystemSetting,
    User,
]


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupSummary:
    created_at: str
    tables: dict[str, int]
    total_rows: int
    encrypted_bytes: int


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    if len(passphrase) < 12:
        raise BackupError("Backup passphrase must contain at least 12 characters")
    return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(passphrase.encode("utf-8"))


def _encode_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return {"$type": "datetime", "value": value.isoformat()}
    if isinstance(value, bytes):
        return {"$type": "bytes", "value": base64.b64encode(value).decode("ascii")}
    return value


def _decode_value(value: Any) -> Any:
    if isinstance(value, dict) and value.get("$type") == "datetime":
        return datetime.fromisoformat(value["value"])
    if isinstance(value, dict) and value.get("$type") == "bytes":
        return base64.b64decode(value["value"])
    return value


def _model_row(instance: Any) -> dict[str, Any]:
    return {column.name: _encode_value(getattr(instance, column.name)) for column in instance.__table__.columns}


async def export_plain_payload(session: AsyncSession) -> dict[str, Any]:
    tables: dict[str, list[dict[str, Any]]] = {}
    for model in TABLE_MODELS:
        primary_keys = list(model.__table__.primary_key.columns)
        query = select(model)
        if primary_keys:
            query = query.order_by(*primary_keys)
        rows = list((await session.scalars(query)).all())
        tables[model.__tablename__] = [_model_row(row) for row in rows]
    return {
        "magic": BACKUP_MAGIC,
        "version": BACKUP_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "app": "PABLO VPN",
        "contains_environment_secrets": False,
        "tables": tables,
    }


async def create_backup(session: AsyncSession, passphrase: str) -> tuple[bytes, BackupSummary]:
    payload = await export_plain_payload(session)
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(passphrase, salt)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, AAD)
    envelope = {
        "magic": BACKUP_MAGIC,
        "version": BACKUP_VERSION,
        "cipher": "AES-256-GCM",
        "kdf": "scrypt-n16384-r8-p1",
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    result = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    counts = {name: len(rows) for name, rows in payload["tables"].items()}
    return result, BackupSummary(
        created_at=payload["created_at"],
        tables=counts,
        total_rows=sum(counts.values()),
        encrypted_bytes=len(result),
    )


def decrypt_backup(data: bytes, passphrase: str) -> dict[str, Any]:
    if not data or len(data) > MAX_BACKUP_BYTES:
        raise BackupError("Backup file is empty or exceeds the 20MB safety limit")
    try:
        envelope = json.loads(data)
        if envelope.get("magic") != BACKUP_MAGIC or envelope.get("version") != BACKUP_VERSION:
            raise BackupError("Unsupported or invalid PABLO backup file")
        salt = base64.b64decode(envelope["salt"], validate=True)
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
        key = _derive_key(passphrase, salt)
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, AAD)
        payload = json.loads(plaintext)
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError("Backup decryption or integrity verification failed") from exc
    validate_payload(payload)
    return payload


def validate_payload(payload: dict[str, Any]) -> None:
    if payload.get("magic") != BACKUP_MAGIC or payload.get("version") != BACKUP_VERSION:
        raise BackupError("Backup payload version is not supported")
    tables = payload.get("tables")
    if not isinstance(tables, dict):
        raise BackupError("Backup has no table collection")
    required = {model.__tablename__ for model in TABLE_MODELS}
    missing = required.difference(tables)
    if missing:
        raise BackupError("Backup is incomplete: " + ", ".join(sorted(missing)))
    for table_name, rows in tables.items():
        if table_name not in required or not isinstance(rows, list):
            raise BackupError(f"Backup table {table_name!r} is invalid")
    if not tables["panel_instances"] or not tables["plans"] or not tables["trial_policy"]:
        raise BackupError("Backup is missing required panel, plan or trial policy data")


def _decoded_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: _decode_value(value) for key, value in row.items()} for row in rows]


async def _reset_postgres_sequences(session: AsyncSession) -> None:
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return
    for table in ["users", "orders", "services", "trial_claims", "audit_logs"]:
        statement = text(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1), "
            f"EXISTS(SELECT 1 FROM {table}))"
        )
        await session.execute(statement)


async def restore_backup(
    session: AsyncSession,
    data: bytes,
    passphrase: str,
    actor_telegram_id: int,
) -> BackupSummary:
    payload = decrypt_backup(data, passphrase)
    tables: dict[str, list[dict[str, Any]]] = payload["tables"]

    for model in RESTORE_DELETE_ORDER:
        await session.execute(delete(model))

    model_by_table = {model.__tablename__: model for model in TABLE_MODELS}
    for model in TABLE_MODELS:
        rows = _decoded_rows(tables[model.__tablename__])
        if rows:
            await session.execute(insert(model_by_table[model.__tablename__]), rows)

    await _reset_postgres_sequences(session)
    session.add(
        AuditLog(
            actor_telegram_id=actor_telegram_id,
            action="backup.restore",
            entity_type="backup",
            entity_id=payload["created_at"],
            details="Full encrypted backup restored successfully",
        )
    )
    await session.flush()
    counts = {name: len(rows) for name, rows in tables.items()}
    return BackupSummary(
        created_at=payload["created_at"],
        tables=counts,
        total_rows=sum(counts.values()),
        encrypted_bytes=len(data),
    )
