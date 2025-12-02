# app/db.py
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, Result
from sqlalchemy.exc import SQLAlchemyError
from contextlib import contextmanager
from typing import Iterator, List, Dict, Any, Optional, Sequence
import logging
import re

from app.config import settings

logger = logging.getLogger(__name__)

engine: Engine = create_engine(
    settings.resolved_database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

@contextmanager
def get_connection():
    conn = engine.connect()
    try:
        yield conn
    finally:
        conn.close()

def execute_readonly_query(
    sql: str,
    params: Optional[dict] = None,
    allowed_objects: Optional[Sequence[str]] = None,
    allowed_columns: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Executes a read-only SQL query and returns rows as dicts.
    This function enforces basic safety: only SELECTs allowed.
    """
    cleaned = sql.strip().rstrip(";").lstrip()
    cleaned_lower = cleaned.lower()

    if not cleaned_lower.startswith("select"):
        raise ValueError("Only SELECT queries are allowed.")

    # Reject common DML/DDL keywords.
    forbidden = ["insert", "update", "delete", "drop", "alter", "truncate", "create"]
    if any(f"\b{kw}\b" in cleaned_lower for kw in forbidden):
        raise ValueError("Only read-only SELECT queries are allowed.")

    # Add a default LIMIT if missing.
    if "limit" not in cleaned_lower:
        cleaned += " LIMIT 200"
        cleaned_lower = cleaned.lower()

    if allowed_objects:
        allowed_lower = [obj.lower() for obj in allowed_objects]
        # Permit schema introspection against information_schema/pg_catalog.
        introspection_ok = "information_schema" in cleaned_lower or "pg_catalog" in cleaned_lower
        if not introspection_ok and not any(obj in cleaned_lower for obj in allowed_lower):
            raise ValueError(
                f"Query must reference one of the allowed objects: {', '.join(allowed_lower)}"
            )

    if allowed_columns:
        cols_lower = set(c.lower() for c in allowed_columns)
        for _, col in re.findall(r"([a-zA-Z_][\\w]*)\\.([a-zA-Z_][\\w]*)", cleaned):
            if col.lower() not in cols_lower:
                raise ValueError(f"Query references unknown column: {col}")

    logger.info("Executing SQL: %s", cleaned)

    try:
        with get_connection() as conn:
            result: Result = conn.execute(text(cleaned), params or {})
            rows = [dict(row._mapping) for row in result]
            return rows
    except SQLAlchemyError as exc:
        # Surface DB errors so the API can return a meaningful message.
        logger.exception("Database execution failed")
        raise ValueError(f"Database execution failed: {exc}") from exc
