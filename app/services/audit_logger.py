from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.audit")


def log_audit_event(
    event_type: str,
    question: str,
    chat_id: Optional[str] = None,
    session_id: Optional[str] = None,
    sql: Optional[str] = None,
    tables: Optional[List[str]] = None,
    row_count: Optional[int] = None,
    action: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    payload: Dict[str, Any] = {
        "event": event_type,
        "action": action,
        "chat_id": chat_id,
        "session_id": session_id,
        "question": question,
        "sql": sql,
        "tables": tables or [],
        "row_count": row_count,
    }
    if extra:
        payload.update(extra)
    logger.info("audit=%s", json.dumps(payload, ensure_ascii=True))
