from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import text

from app.db import get_connection


@dataclass
class ResultMemory:
    chat_id: str
    message_id: str
    snapshot: Dict[str, Any]
    created_at: datetime


def get_result_snapshot(chat_id: str, message_id: str) -> Optional[ResultMemory]:
    with get_connection() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, chat_id, metadata, created_at
                FROM messages
                WHERE id = :message_id
                  AND chat_id = :chat_id
                  AND role = 'assistant'
                  AND metadata ? 'result_snapshot'
                LIMIT 1
                """
            ),
            {"chat_id": chat_id, "message_id": message_id},
        ).fetchone()

    if not row:
        return None

    metadata = row._mapping.get("metadata") or {}
    snapshot = metadata.get("result_snapshot")
    if not snapshot:
        return None

    return ResultMemory(
        chat_id=str(row._mapping.get("chat_id")),
        message_id=str(row._mapping.get("id")),
        snapshot=snapshot,
        created_at=row._mapping.get("created_at"),
    )


def get_last_result_snapshot(chat_id: str) -> Optional[ResultMemory]:
    with get_connection() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, chat_id, metadata, created_at
                FROM messages
                WHERE chat_id = :chat_id
                  AND role = 'assistant'
                  AND metadata ? 'result_snapshot'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"chat_id": chat_id},
        ).fetchone()

    if not row:
        return None

    metadata = row._mapping.get("metadata") or {}
    snapshot = metadata.get("result_snapshot")
    if not snapshot:
        return None

    return ResultMemory(
        chat_id=str(row._mapping.get("chat_id")),
        message_id=str(row._mapping.get("id")),
        snapshot=snapshot,
        created_at=row._mapping.get("created_at"),
    )
