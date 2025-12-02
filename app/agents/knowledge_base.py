# app/agents/knowledge_base.py
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings

class KnowledgeBase:
    def __init__(self, metadata_path: Optional[Path] = None):
        self.metadata_path = metadata_path or settings.metadata_path
        self._data: Dict[str, Any] = {}
        self._schema_text: Optional[str] = None
        self._load()

    def _load(self) -> None:
        with self.metadata_path.open() as f:
            self._data = json.load(f)

    @property
    def dialect(self) -> str:
        return self._data.get("dialect", "postgresql")

    @property
    def default_schema(self) -> str:
        return self._data.get("default_schema") or self._data.get("defaultschema", "public")

    @property
    def views(self) -> List[Dict[str, Any]]:
        return self._data.get("views", [])

    @property
    def tables(self) -> List[Dict[str, Any]]:
        return self._data.get("tables", [])

    def get_view(self, name: str) -> Optional[Dict[str, Any]]:
        for v in self.views:
            if v["name"] == name:
                return v
        return None

    def allowed_objects(self) -> List[str]:
        """Return fully qualified allowed view/table names."""
        allowed: List[str] = []
        for v in self.views:
            allowed.append(f"{v.get('schema', self.default_schema)}.{v['name']}".lower())
        for t in self.tables:
            allowed.append(f"{t.get('schema', self.default_schema)}.{t['name']}".lower())
        return allowed

    def allowed_columns(self) -> List[str]:
        """Return all column names across tables/views (lowercased)."""
        cols: List[str] = []
        for t in self.tables:
            for c in t.get("columns", []):
                cols.append(c["name"].lower())
        for v in self.views:
            for c in v.get("columns", []):
                cols.append(c["name"].lower())
        return cols

    def as_schema_text(self) -> str:
        """Return a human-readable schema description for the LLM."""
        if self._schema_text is not None:
            return self._schema_text

        lines: List[str] = []

        for t in self.tables:
            fq_name = f"{t.get('schema', self.default_schema)}.{t['name']}"
            lines.append(f"Table: {fq_name}")
            if t.get("description"):
                lines.append(f"  Description: {t['description']}")
            lines.append("  Columns:")
            for c in t.get("columns", []):
                col_desc = c.get("description", "")
                col_type = c.get("type", "")
                lines.append(f"    - {c['name']} ({col_type}) - {col_desc}")
            lines.append("")

        for v in self.views:
            fq_name = f"{v.get('schema', self.default_schema)}.{v['name']}"
            lines.append(f"View: {fq_name}")
            if v.get("description"):
                lines.append(f"  Description: {v['description']}")
            lines.append("  Columns:")
            for c in v.get("columns", []):
                col_desc = c.get("description", "")
                col_type = c.get("type", "")
                lines.append(f"    - {c['name']} ({col_type}) - {col_desc}")
            lines.append("")

        self._schema_text = "\n".join(lines).strip()
        return self._schema_text
