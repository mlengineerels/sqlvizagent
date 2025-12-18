from typing import Any, Dict, List, Optional

from app.config import settings
from app.vector_store import VectorStore


class KnowledgeBase:
    """
    Vector-first knowledge base. Uses pgvector entries only; no metadata.json fallback.
    """

    def __init__(self, vector_store: Optional[VectorStore] = None):
        self.vector_store = vector_store
        self._vector_entries: List[Dict[str, Any]] = []
        self._schema_text: Optional[str] = None
        self._load_vector_entries()

    def _load_vector_entries(self) -> None:
        if not self.vector_store:
            self._vector_entries = []
            return
        try:
            self._vector_entries = self.vector_store.get_all_entries()
        except Exception:
            self._vector_entries = []

    def refresh_vector_entries(self) -> None:
        self._schema_text = None
        self._load_vector_entries()

    @property
    def dialect(self) -> str:
        # Default to postgres; adjust via env if needed.
        return "postgresql"

    @property
    def default_schema(self) -> str:
        return "public"

    @property
    def tables(self) -> List[Dict[str, Any]]:
        return [e for e in self._vector_entries if e.get("object_type") == "table"]

    @property
    def views(self) -> List[Dict[str, Any]]:
        return [e for e in self._vector_entries if e.get("object_type") == "view"]

    def get_view(self, name: str) -> Optional[Dict[str, Any]]:
        for v in self.views:
            if v.get("name") == name:
                return v
        return None

    def allowed_objects(self) -> List[str]:
        """Return fully qualified allowed view/table names."""
        allowed: List[str] = []
        default_schema = self.default_schema
        for v in self.views:
            allowed.append(f"{v.get('schema', default_schema)}.{v.get('name','')}".lower())
        for t in self.tables:
            allowed.append(f"{t.get('schema', default_schema)}.{t.get('name','')}".lower())
        return [a for a in allowed if a.strip()]

    def allowed_columns(self) -> List[str]:
        """Return all column names across tables/views (lowercased)."""
        cols: List[str] = []
        for t in self.tables:
            for c in t.get("columns", []):
                name = c.get("name", "")
                if name:
                    cols.append(name.lower())
        for v in self.views:
            for c in v.get("columns", []):
                name = c.get("name", "")
                if name:
                    cols.append(name.lower())
        return cols

    def as_schema_text(self) -> str:
        """Return a human-readable schema description for the LLM."""
        if self._schema_text is not None:
            return self._schema_text

        lines: List[str] = []
        default_schema = self.default_schema

        for t in self.tables:
            fq_name = f"{t.get('schema', default_schema)}.{t.get('name','')}"
            lines.append(f"Table: {fq_name}")
            if t.get("description"):
                lines.append(f"  Description: {t['description']}")
            lines.append("  Columns:")
            for c in t.get("columns", []):
                col_desc = c.get("description", "")
                col_type = c.get("type", "")
                lines.append(f"    - {c.get('name','')} ({col_type}) - {col_desc}")
            lines.append("")

        for v in self.views:
            fq_name = f"{v.get('schema', default_schema)}.{v.get('name','')}"
            lines.append(f"View: {fq_name}")
            if v.get("description"):
                lines.append(f"  Description: {v['description']}")
            lines.append("  Columns:")
            for c in v.get("columns", []):
                col_desc = c.get("description", "")
                col_type = c.get("type", "")
                lines.append(f"    - {c.get('name','')} ({col_type}) - {col_desc}")
            lines.append("")

        self._schema_text = "\n".join(lines).strip()
        return self._schema_text
