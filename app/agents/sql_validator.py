from __future__ import annotations

import logging
from typing import List, Optional, Sequence, Tuple

try:
    from sqlglot import expressions as exp
    from sqlglot import parse_one
except ImportError as exc:  # pragma: no cover - dependency is declared in requirements
    raise ImportError("sqlglot is required for SQL validation") from exc

logger = logging.getLogger(__name__)


class SQLValidator:
    """
    Stronger SQL validation using sqlglot instead of regex/substring checks.
    Ensures SELECT-only, allowed objects/columns, and enforces a max LIMIT.
    """

    def __init__(
        self,
        allowed_objects: Sequence[str],
        allowed_columns: Sequence[str],
        max_limit: int = 200,
    ) -> None:
        self.allowed_objects = {obj.lower() for obj in allowed_objects}
        # Also allow unqualified table names for convenience.
        self.allowed_object_names = {obj.split(".")[-1] for obj in self.allowed_objects}
        self.allowed_columns = {col.lower() for col in allowed_columns}
        self.max_limit = max_limit

    def _format_table(self, table_expr: exp.Table) -> str:
        """
        sqlglot represents schema as `db`; depending on version it can be an Identifier or a raw string.
        """
        db = table_expr.db
        if isinstance(db, exp.Identifier):
            schema = db.name
        elif isinstance(db, str):
            schema = db
        else:
            schema = ""

        name = table_expr.name
        fq = f"{schema}.{name}" if schema else name
        return fq.lower()

    def _is_introspection(self, table_name: str) -> bool:
        return table_name.startswith("information_schema") or table_name.startswith("pg_catalog")

    def _get_limit_value(self, limit_expr: Optional[exp.Expression]) -> Optional[int]:
        if not limit_expr:
            return None
        # sqlglot may store the limit under "expression" or "this" depending on version.
        candidate = limit_expr.args.get("expression") or limit_expr.args.get("this")
        if isinstance(candidate, exp.Literal) and candidate.is_number:
            try:
                return int(candidate.this)
            except (TypeError, ValueError):
                return None
        return None

    def _set_limit_value(self, limit_expr: exp.Expression, value: int) -> None:
        lit = exp.Literal.number(value)
        if "expression" in limit_expr.args:
            limit_expr.set("expression", lit)
        else:
            limit_expr.set("this", lit)

    def validate(self, sql: str) -> Tuple[str, List[str]]:
        cleaned = sql.strip().rstrip(";")
        notes: List[str] = []

        try:
            expr = parse_one(cleaned, read="postgres")
        except Exception as exc:
            raise ValueError(f"Failed to parse SQL: {exc}") from exc

        # Reject any DML/DDL.
        forbidden_nodes = (exp.Insert, exp.Delete, exp.Update, exp.Create, exp.Drop, exp.Alter)
        if expr.find(forbidden_nodes):
            raise ValueError("Only read-only SELECT queries are allowed.")

        # Ensure the root is a SELECT/UNION construct.
        if not isinstance(expr, (exp.Select, exp.Subquery, exp.Union, exp.With, exp.Values)):
            raise ValueError("Query must be a SELECT statement.")

        # Enforce allowed tables/views.
        for table_expr in expr.find_all(exp.Table):
            table_name = self._format_table(table_expr)
            simple = table_name.split(".")[-1]
            if self._is_introspection(table_name):
                continue
            if table_name not in self.allowed_objects and simple not in self.allowed_object_names:
                raise ValueError(f"Query references disallowed object: {table_name}")

        # Enforce allowed columns where resolvable.
        for col in expr.find_all(exp.Column):
            name = col.name
            if not name or name == "*":
                continue
            if name.lower() not in self.allowed_columns:
                raise ValueError(f"Query references unknown column: {name}")

        # Enforce LIMIT.
        limit_expr = expr.find(exp.Limit)
        if not limit_expr:
            cleaned_with_limit = cleaned + f" LIMIT {self.max_limit}"
            notes.append(f"Added default LIMIT {self.max_limit}")
            expr = parse_one(cleaned_with_limit, read="postgres")
            limit_expr = expr.find(exp.Limit)
        current_limit = self._get_limit_value(limit_expr)
        if current_limit and current_limit > self.max_limit:
            self._set_limit_value(limit_expr, self.max_limit)
            notes.append(f"Capped LIMIT from {current_limit} to {self.max_limit}")

        safe_sql = expr.sql(dialect="postgres")
        logger.info("Validated SQL -> %s", safe_sql)
        return safe_sql, notes
