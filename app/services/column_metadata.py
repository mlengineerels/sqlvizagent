from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    from sqlglot import parse_one, exp
except ImportError as exc:  # pragma: no cover
    raise ImportError("sqlglot is required for column metadata lookup") from exc

logger = logging.getLogger(__name__)


class ColumnMetadataStore:
    def __init__(self, metadata_path: Path) -> None:
        self.metadata_path = metadata_path
        self._index: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None
        self._default_schema: Optional[str] = None

    def _load(self) -> None:
        if self._index is not None:
            return
        try:
            raw = json.loads(Path(self.metadata_path).read_text())
        except Exception as exc:
            logger.warning("Failed to load metadata file: %s", exc)
            self._index = {}
            self._default_schema = None
            return

        self._default_schema = raw.get("defaultschema")
        index: Dict[str, Dict[str, Dict[str, Any]]] = {}

        for group in ("tables", "views"):
            for table in raw.get(group, []) or []:
                name = table.get("name")
                if not name:
                    continue
                schema = table.get("schema") or self._default_schema or ""
                fq_name = f"{schema}.{name}" if schema else name
                columns: Dict[str, Dict[str, Any]] = {}
                for col in table.get("columns", []) or []:
                    col_name = col.get("name")
                    if not col_name:
                        continue
                    columns[col_name.lower()] = {
                        "name": col_name,
                        "type": col.get("type"),
                        "description": col.get("description"),
                    }
                index[fq_name.lower()] = columns

        self._index = index

    def _format_table(self, table_expr: exp.Table) -> str:
        db = table_expr.db
        if isinstance(db, exp.Identifier):
            schema = db.name
        elif isinstance(db, str):
            schema = db
        else:
            schema = ""

        name = table_expr.name
        if not name:
            return ""
        if not schema and self._default_schema:
            schema = self._default_schema
        return f"{schema}.{name}" if schema else name

    def _table_names_from_sql(self, sql: str) -> Set[str]:
        if not sql:
            return set()
        try:
            expr = parse_one(sql, read="postgres")
        except Exception:
            return set()

        cte_names = {
            cte.alias_or_name.lower()
            for cte in expr.find_all(exp.CTE)
            if cte.alias_or_name
        }

        tables: Set[str] = set()
        for table_expr in expr.find_all(exp.Table):
            table_name = self._format_table(table_expr).lower()
            if not table_name:
                continue
            simple = table_name.split(".")[-1]
            if table_name in cte_names or simple in cte_names:
                continue
            tables.add(table_name)
        return tables

    def extract_table_names(self, sql: str) -> List[str]:
        self._load()
        tables = self._table_names_from_sql(sql or "")
        return sorted(tables)

    def describe_columns(self, columns: List[str], sql: Optional[str] = None) -> Dict[str, Any]:
        self._load()
        if not columns or self._index is None:
            return {}

        tables = self._table_names_from_sql(sql or "")
        candidates = tables if tables else set(self._index.keys())

        result: Dict[str, Any] = {}
        for col in columns:
            col_key = col.lower()
            matches = []
            for table_name in candidates:
                table_columns = self._index.get(table_name)
                if not table_columns:
                    continue
                meta = table_columns.get(col_key)
                if meta:
                    matches.append({"table": table_name, **meta})
            if matches:
                result[col] = matches
        return result
