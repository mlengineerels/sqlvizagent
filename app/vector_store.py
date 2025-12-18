from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import openai
from sqlalchemy import text

from app.config import settings
from app.db import get_connection

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI  # type: ignore
except ImportError:  # pragma: no cover - compatibility with older openai
    OpenAI = None  # type: ignore


def _embedding_to_literal(vec: List[float]) -> str:
    return "[" + ",".join(f"{v}" for v in vec) + "]"


def _build_schema_entry(obj: Dict[str, Any], object_type: str, default_schema: str) -> Dict[str, Any]:
    schema = obj.get("schema", default_schema)
    name = obj["name"]
    description = obj.get("description", "")
    columns = obj.get("columns", [])
    col_text = "; ".join(f"{c.get('name')}: {c.get('description','')}" for c in columns)
    text_blob = f"{object_type.upper()} {schema}.{name}\nDescription: {description}\nColumns: {col_text}"
    return {
        "schema": schema,
        "name": name,
        "object_type": object_type,
        "description": description,
        "columns": columns,
        "text": text_blob,
    }


class VectorStore:
    def __init__(self) -> None:
        self.model = settings.openai_embedding_model
        if OpenAI:
            self.client = OpenAI(api_key=settings.openai_api_key)
            self.use_client = True
        else:
            openai.api_key = settings.openai_api_key
            self.client = None
            self.use_client = False

    def _embed(self, text_content: str) -> List[float]:
        if self.use_client:
            resp = self.client.embeddings.create(model=self.model, input=text_content)
            return resp.data[0].embedding  # type: ignore[attr-defined]
        resp = openai.Embedding.create(model=self.model, input=text_content)
        return resp["data"][0]["embedding"]

    def ensure_extension_and_table(self) -> None:
        dim = settings.embedding_dimensions
        with get_connection() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS schema_embeddings (
                        id serial PRIMARY KEY,
                        name text NOT NULL,
                        object_type text NOT NULL,
                        schema_name text NOT NULL,
                        description text,
                        columns jsonb,
                        embedding vector(:dim)
                    )
                    """
                ),
                {"dim": dim},
            )
            conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS schema_embeddings_embedding_idx
                    ON schema_embeddings USING ivfflat (embedding vector_cosine_ops)
                    WITH (lists = 100)
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS schema_embeddings_unique
                    ON schema_embeddings (name, object_type, schema_name)
                    """
                )
            )
            conn.commit()

    def sync_metadata(self, metadata: Dict[str, Any]) -> None:
        self.ensure_extension_and_table()
        default_schema = metadata.get("default_schema") or metadata.get("defaultschema", "public")
        entries: List[Dict[str, Any]] = []
        for t in metadata.get("tables", []):
            entries.append(_build_schema_entry(t, "table", default_schema))
        for v in metadata.get("views", []):
            entries.append(_build_schema_entry(v, "view", default_schema))

        with get_connection() as conn:
            try:
                existing = conn.execute(
                    text("SELECT name, object_type, schema_name FROM schema_embeddings")
                ).fetchall()
            except Exception as exc:
                # If the table somehow does not exist yet, ensure and retry once.
                logger.warning("schema_embeddings missing; creating. Detail: %s", exc)
                self.ensure_extension_and_table()
                existing = conn.execute(
                    text("SELECT name, object_type, schema_name FROM schema_embeddings")
                ).fetchall()

            existing_keys = {(row[0], row[1], row[2]) for row in existing}

            for entry in entries:
                key = (entry["name"], entry["object_type"], entry["schema"])
                if key in existing_keys:
                    continue
                embedding = self._embed(entry["text"])
                conn.execute(
                    text(
                        """
                        INSERT INTO schema_embeddings (name, object_type, schema_name, description, columns, embedding)
                        VALUES (:name, :object_type, :schema_name, :description, :columns, :embedding)
                        """
                    ),
                    {
                        "name": entry["name"],
                        "object_type": entry["object_type"],
                        "schema_name": entry["schema"],
                        "description": entry["description"],
                        "columns": json.dumps(entry["columns"]),
                        "embedding": _embedding_to_literal(embedding),
                    },
                )
            conn.commit()

    def get_relevant_schema(self, question: str, top_k: int = 5) -> str:
        try:
            query_embedding = self._embed(question)
        except Exception as exc:
            logger.warning("Embedding failed: %s", exc)
            return ""

        query_vec = _embedding_to_literal(query_embedding)
        with get_connection() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT name, object_type, schema_name, description, columns
                    FROM schema_embeddings
                    ORDER BY embedding <#> :query_vec
                    LIMIT :k
                    """
                ),
                {"query_vec": query_vec, "k": top_k},
            ).fetchall()

        lines: List[str] = []
        for row in rows:
            name, obj_type, schema_name, description, columns_json = row
            cols = columns_json or []
            col_text = "; ".join(f"{c.get('name')}: {c.get('description','')}" for c in cols)
            lines.append(f"{obj_type.upper()} {schema_name}.{name} — {description}")
            if col_text:
                lines.append(f"  Columns: {col_text}")
        return "\n".join(lines)

    def get_all_entries(self) -> List[Dict[str, Any]]:
        """Return all stored schema entries."""
        with get_connection() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT name, object_type, schema_name, description, columns
                    FROM schema_embeddings
                    """
                )
            ).fetchall()
        entries: List[Dict[str, Any]] = []
        for row in rows:
            name, obj_type, schema_name, description, columns_json = row
            entries.append(
                {
                    "name": name,
                    "object_type": obj_type,
                    "schema": schema_name,
                    "description": description,
                    "columns": columns_json or [],
                }
            )
        return entries
