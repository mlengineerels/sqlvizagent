from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.db import get_connection


def add_feedback(
    chat_id: str,
    message_id: str,
    rating: int,
    comment: Optional[str] = None,
) -> Dict[str, Any]:
    with get_connection() as conn:
        with conn.begin():
            row = conn.execute(
                text(
                    """
                    INSERT INTO feedback (chat_id, message_id, rating, comment)
                    VALUES (:chat_id, :message_id, :rating, :comment)
                    ON CONFLICT (message_id)
                    DO UPDATE SET rating = :rating, comment = :comment, created_at = now()
                    RETURNING id, chat_id, message_id, rating, comment, created_at
                    """
                ),
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "rating": rating,
                    "comment": comment,
                },
            ).fetchone()

    if not row:
        raise ValueError("Failed to store feedback")
    data = dict(row._mapping)
    for key in ("id", "chat_id", "message_id"):
        if key in data and data[key] is not None:
            data[key] = str(data[key])
    return data


def list_feedback(chat_id: str) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, chat_id, message_id, rating, comment, created_at
                FROM feedback
                WHERE chat_id = :chat_id
                ORDER BY created_at ASC
                """
            ),
            {"chat_id": chat_id},
        ).fetchall()

    items = []
    for r in rows:
        data = dict(r._mapping)
        for key in ("id", "chat_id", "message_id"):
            if key in data and data[key] is not None:
                data[key] = str(data[key])
        items.append(data)
    return items
