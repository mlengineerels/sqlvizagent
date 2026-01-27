from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import text

from app.db import get_connection


def _row_to_dict(row) -> Dict[str, Any]:
    data = dict(row._mapping)
    if "id" in data:
        data["id"] = str(data["id"])
    if "chat_id" in data:
        data["chat_id"] = str(data["chat_id"])
    return data


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    return value


def create_chat(title: str) -> str:
    with get_connection() as conn:
        with conn.begin():
            row = conn.execute(
                text("INSERT INTO chats (title) VALUES (:title) RETURNING id"),
                {"title": title},
            ).fetchone()
    if not row:
        raise ValueError("Failed to create chat")
    return str(row[0])


def list_chats() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, title, created_at, updated_at
                FROM chats
                ORDER BY updated_at DESC
                """
            )
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_chat(chat_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        chat_row = conn.execute(
            text(
                """
                SELECT id, title, created_at, updated_at
                FROM chats
                WHERE id = :chat_id
                """
            ),
            {"chat_id": chat_id},
        ).fetchone()

        if not chat_row:
            return None

        msg_rows = conn.execute(
            text(
                """
                SELECT id, chat_id, role, content, metadata, created_at
                FROM messages
                WHERE chat_id = :chat_id
                ORDER BY created_at ASC
                """
            ),
            {"chat_id": chat_id},
        ).fetchall()

    return {
        "chat": _row_to_dict(chat_row),
        "messages": [_row_to_dict(r) for r in msg_rows],
    }


def rename_chat(chat_id: str, title: str) -> None:
    with get_connection() as conn:
        with conn.begin():
            conn.execute(
                text(
                    """
                    UPDATE chats
                    SET title = :title, updated_at = now()
                    WHERE id = :chat_id
                    """
                ),
                {"chat_id": chat_id, "title": title},
            )


def delete_chat(chat_id: str) -> None:
    with get_connection() as conn:
        with conn.begin():
            conn.execute(
                text("DELETE FROM chats WHERE id = :chat_id"),
                {"chat_id": chat_id},
            )


def append_message(
    chat_id: str,
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    safe_metadata = _json_safe(metadata) if metadata is not None else None
    metadata_payload = json.dumps(safe_metadata) if safe_metadata is not None else None
    with get_connection() as conn:
        with conn.begin():
            row = conn.execute(
                text(
                    """
                    INSERT INTO messages (chat_id, role, content, metadata)
                    VALUES (:chat_id, :role, :content, CAST(:metadata AS jsonb))
                    RETURNING id
                    """
                ),
                {
                    "chat_id": chat_id,
                    "role": role,
                    "content": content,
                    "metadata": metadata_payload,
                },
            ).fetchone()

            conn.execute(
                text("UPDATE chats SET updated_at = now() WHERE id = :chat_id"),
                {"chat_id": chat_id},
            )

    if not row:
        raise ValueError("Failed to append message")
    return str(row[0])


def maybe_autotitle_chat(chat_id: str, question: str) -> Optional[str]:
    title = " ".join(str(question).strip().split()[:6]).strip()
    if not title:
        return None
    with get_connection() as conn:
        with conn.begin():
            row = conn.execute(
                text("SELECT title FROM chats WHERE id = :chat_id"),
                {"chat_id": chat_id},
            ).fetchone()
            if not row:
                return None
            current = row[0] or ""
            if current.strip().lower() != "new chat":
                return None
            conn.execute(
                text(
                    """
                    UPDATE chats
                    SET title = :title, updated_at = now()
                    WHERE id = :chat_id
                    """
                ),
                {"chat_id": chat_id, "title": title},
            )
    return title
