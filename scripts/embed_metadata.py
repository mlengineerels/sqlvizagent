"""
One-off embedding sync script.

Reads config/metadata.json and upserts entries into schema_embeddings with pgvector embeddings.
Run on a schedule (e.g., daily) after updating metadata.json.
"""

import json
from pathlib import Path

import openai
from sqlalchemy import create_engine, text

from app.config import settings
from app.vector_store import _embedding_to_literal, _build_schema_entry  # type: ignore


def embed_text(model: str, text_content: str) -> list[float]:
    resp = openai.Embedding.create(model=model, input=text_content)
    return resp["data"][0]["embedding"]


def ensure_schema(engine) -> None:
    dim = settings.embedding_dimensions
    with engine.begin() as conn:
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


def upsert_metadata(engine, metadata_path: Path) -> None:
    with metadata_path.open() as f:
        metadata = json.load(f)

    default_schema = metadata.get("default_schema") or metadata.get("defaultschema", "public")
    entries = []
    for t in metadata.get("tables", []):
        entries.append(_build_schema_entry(t, "table", default_schema))
    for v in metadata.get("views", []):
        entries.append(_build_schema_entry(v, "view", default_schema))

    with engine.begin() as conn:
        # Clear existing embeddings to fully refresh.
        conn.execute(text("TRUNCATE schema_embeddings"))
        for entry in entries:
            embedding = embed_text(settings.openai_embedding_model, entry["text"])
            conn.execute(
                text(
                    """
                    INSERT INTO schema_embeddings (name, object_type, schema_name, description, columns, embedding)
                    VALUES (:name, :object_type, :schema_name, :description, :columns, :embedding)
                    ON CONFLICT (name, object_type, schema_name)
                    DO UPDATE SET description = EXCLUDED.description,
                                  columns = EXCLUDED.columns,
                                  embedding = EXCLUDED.embedding
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


def main():
    openai.api_key = settings.openai_api_key
    engine = create_engine(settings.resolved_database_url, future=True)
    ensure_schema(engine)
    metadata_path = Path("config/metadata.json")
    upsert_metadata(engine, metadata_path)
    print("Schema embeddings upserted successfully.")


if __name__ == "__main__":
    main()
