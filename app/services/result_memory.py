from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, List

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


def list_recent_result_snapshots(chat_id: str, limit: int = 5) -> List[ResultMemory]:
    if limit <= 0:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, chat_id, metadata, created_at
                FROM messages
                WHERE chat_id = :chat_id
                  AND role = 'assistant'
                  AND metadata ? 'result_snapshot'
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"chat_id": chat_id, "limit": limit},
        ).fetchall()

    results: List[ResultMemory] = []
    for row in rows:
        metadata = row._mapping.get("metadata") or {}
        snapshot = metadata.get("result_snapshot")
        if not snapshot:
            continue
        results.append(
            ResultMemory(
                chat_id=str(row._mapping.get("chat_id")),
                message_id=str(row._mapping.get("id")),
                snapshot=snapshot,
                created_at=row._mapping.get("created_at"),
            )
        )
    return results
