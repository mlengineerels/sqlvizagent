# app/services/query_service.py
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from app.agents.knowledge_base import KnowledgeBase
from app.agents.router import RouterAgent
from app.agents.sql_agent import SQLAgent
from app.agents.sql_refiner import SQLRefiner
from app.agents.viz_agent import VizAgent
from app.agents.table_agent import TableAgent
from app.agents.response_interpreter import ResponseInterpreter
from app.agents.context_resolver import ContextResolver
from app.agents.followup_strategy import FollowupStrategyResolver
from app.agents.followup_rewriter import FollowupRewriter
from app.config import settings
from app.services.agent_controller import AgentController, ControllerResult
from app.services.audit_logger import log_audit_event
from app.services.chat_store import append_message, maybe_autotitle_chat
from app.services.result_memory import get_last_result_snapshot, get_result_snapshot
from app.services.orchestrator import Orchestrator
from app.agents.sql_validator import SQLValidator
from app.vector_store import VectorStore
from app.db import execute_readonly_query
from app.services.column_metadata import ColumnMetadataStore
try:
    from sqlglot import parse_one, exp
except ImportError as exc:  # pragma: no cover
    raise ImportError("sqlglot is required for refinement validation") from exc
from types import SimpleNamespace

logger = logging.getLogger(__name__)
RESULT_SAMPLE_SIZE = 20
HIDDEN_PK_PREFIX = "__pk_"


@dataclass
class QueryResponse:
    sql: str
    rows: List[Dict[str, Any]]
    figure: Optional[Dict[str, Any]] = None
    intent: Optional[str] = None
    suggested_tables: Optional[List[str]] = None
    trace: Optional[List[Dict[str, Any]]] = None
    plan: Optional[List[Dict[str, Any]]] = None
    duration_ms: Optional[int] = None
    notes: Optional[List[str]] = None
    result_snapshot: Optional[Dict[str, Any]] = None
    action: Optional[str] = None
    assistant_text: Optional[str] = None
    fallback_question: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None


class QueryService:
    def __init__(self, kb: Optional[KnowledgeBase] = None):
        # Vector-only KB; assumes embeddings are pre-populated externally.
        self.vector_store: Optional[VectorStore] = None
        try:
            self.vector_store = VectorStore()
        except Exception as exc:
            logger.warning("Vector store init failed: %s", exc)
            self.vector_store = None

        self.kb = kb or KnowledgeBase(vector_store=self.vector_store)
        self.router = RouterAgent()
        self.sql_agent = SQLAgent(self.kb, vector_store=self.vector_store)
        self.sql_refiner = SQLRefiner()
        self.viz_agent = VizAgent(self.kb)
        self.table_agent = TableAgent(self.kb)
        self.interpreter = ResponseInterpreter()
        self.context_resolver = ContextResolver()
        self.followup_strategy = FollowupStrategyResolver()
        self.followup_rewriter = FollowupRewriter()
        self.column_metadata = ColumnMetadataStore(settings.metadata_path)
        self.cache: Optional[Dict[str, List[Dict[str, Any]]]] = {} if settings.enable_query_cache else None
        self.controller = AgentController(
            kb=self.kb,
            vector_store=self.vector_store,
            sql_agent=self.sql_agent,
            viz_agent=self.viz_agent,
            cache=self.cache,
        )
        self.orchestrator = Orchestrator(self)

    def _normalize_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, str):
            return value
        return str(value)

    def _profile_columns(self, rows_sample: List[Dict[str, Any]], columns: List[str]) -> Dict[str, Any]:
        stats: Dict[str, Any] = {"sample_size": len(rows_sample), "columns": {}}
        for col in columns:
            type_counts = {"number": 0, "string": 0, "bool": 0, "datetime": 0, "other": 0}
            null_count = 0
            distinct: set = set()
            num_sum = 0.0
            num_count = 0
            num_min: Optional[float] = None
            num_max: Optional[float] = None
            dt_min: Optional[datetime] = None
            dt_max: Optional[datetime] = None
            categorical_counts: Dict[Any, int] = {}

            for row in rows_sample:
                value = (row or {}).get(col)
                if value is None:
                    null_count += 1
                    continue

                normalized = self._normalize_value(value)
                distinct.add(normalized)

                if isinstance(value, bool):
                    type_counts["bool"] += 1
                    categorical_counts[value] = categorical_counts.get(value, 0) + 1
                elif isinstance(value, (int, float, Decimal)):
                    type_counts["number"] += 1
                    num = float(value)
                    num_sum += num
                    num_count += 1
                    num_min = num if num_min is None else min(num_min, num)
                    num_max = num if num_max is None else max(num_max, num)
                elif isinstance(value, (datetime, date)):
                    type_counts["datetime"] += 1
                    dt = value if isinstance(value, datetime) else datetime.combine(value, datetime.min.time())
                    dt_min = dt if dt_min is None else min(dt_min, dt)
                    dt_max = dt if dt_max is None else max(dt_max, dt)
                elif isinstance(value, str):
                    type_counts["string"] += 1
                    categorical_counts[value] = categorical_counts.get(value, 0) + 1
                else:
                    type_counts["other"] += 1
                    categorical_counts[str(value)] = categorical_counts.get(str(value), 0) + 1

            dominant_type = "null"
            if any(type_counts.values()):
                dominant_type = max(type_counts, key=type_counts.get)

            profile: Dict[str, Any] = {
                "type": dominant_type,
                "null_count": null_count,
                "distinct_count": len(distinct),
            }

            if dominant_type == "number" and num_count:
                profile["min"] = num_min
                profile["max"] = num_max
                profile["avg"] = num_sum / num_count
            elif dominant_type == "datetime":
                profile["min"] = dt_min.isoformat() if dt_min else None
                profile["max"] = dt_max.isoformat() if dt_max else None
            elif dominant_type in {"string", "bool", "other"} and categorical_counts:
                top_values = sorted(
                    categorical_counts.items(),
                    key=lambda item: (-item[1], str(item[0])),
                )[:3]
                profile["top_values"] = [
                    {"value": value, "count": count} for value, count in top_values
                ]

            stats["columns"][col] = profile

        return stats

    def _strip_hidden_columns(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not rows:
            return []
        filtered: List[Dict[str, Any]] = []
        for row in rows:
            if not row:
                filtered.append({})
                continue
            filtered.append(
                {
                    key: value
                    for key, value in row.items()
                    if not key.startswith(HIDDEN_PK_PREFIX)
                }
            )
        return filtered

    def _split_snapshot_columns(self, snapshot: Dict[str, Any]) -> Tuple[List[str], List[str]]:
        columns = snapshot.get("columns")
        if isinstance(columns, dict):
            display = list(columns.get("display") or [])
            hidden = list(columns.get("hidden") or [])
            return display, hidden
        if isinstance(columns, list):
            return list(columns), []
        return [], []

    def _alias_map_from_sql(self, sql: str) -> Dict[str, str]:
        alias_map: Dict[str, str] = {}
        if not sql:
            return alias_map
        try:
            expr = parse_one(sql, read="postgres")
        except Exception:
            return alias_map
        for table_expr in expr.find_all(exp.Table):
            if table_expr.find_ancestor(exp.CTE):
                continue
            table_name = self.column_metadata.format_table_expr(table_expr).lower()
            alias = (table_expr.alias_or_name or table_expr.name or "").lower()
            if alias:
                alias_map[alias] = table_name
        return alias_map

    def _outer_select(self, expr: exp.Expression) -> Optional[exp.Select]:
        if isinstance(expr, exp.With):
            expr = expr.this
        if isinstance(expr, exp.Subquery):
            expr = expr.this
        if isinstance(expr, exp.Select):
            return expr
        if isinstance(expr, exp.Union):
            return None
        return expr.find(exp.Select)

    def _ensure_hidden_pk_columns(
        self,
        sql: str,
        entity_table: Optional[str],
        primary_key: List[str],
    ) -> Tuple[str, List[str]]:
        if not sql or not entity_table or not primary_key:
            return sql, []
        try:
            expr = parse_one(sql, read="postgres")
        except Exception:
            return sql, []
        select_expr = self._outer_select(expr)
        if not select_expr:
            return sql, []

        existing_hidden = [
            alias.alias_or_name
            for alias in select_expr.expressions
            if isinstance(alias, exp.Alias)
            and alias.alias_or_name
            and alias.alias_or_name.lower().startswith(HIDDEN_PK_PREFIX)
        ]

        if select_expr.find(exp.Group) or select_expr.find(exp.AggFunc):
            return sql, existing_hidden

        alias_map = self._alias_map_from_sql(sql)
        entity_table_l = entity_table.lower()
        entity_alias: Optional[str] = None
        for alias, table_name in alias_map.items():
            if table_name == entity_table_l or table_name.endswith(f".{entity_table_l.split('.')[-1]}"):
                entity_alias = alias
                break
        if not entity_alias:
            return sql, existing_hidden

        existing_aliases = {
            alias.alias_or_name.lower()
            for alias in select_expr.expressions
            if isinstance(alias, exp.Alias) and alias.alias_or_name
        }
        existing_columns = {
            col.name.lower()
            for col in select_expr.expressions
            if isinstance(col, exp.Column) and col.name
        }

        hidden_aliases: List[str] = list(existing_hidden)
        for pk in primary_key:
            hidden_alias = f"{HIDDEN_PK_PREFIX}{pk}"
            hidden_aliases.append(hidden_alias)
            if hidden_alias.lower() in existing_aliases or hidden_alias.lower() in existing_columns:
                continue
            col_expr = exp.column(pk, table=entity_alias)
            select_expr.select(exp.alias_(col_expr, hidden_alias), append=True)
            group_expr = select_expr.args.get("group")
            if group_expr:
                group_cols = list(group_expr.expressions)
                group_sql = {g.sql(dialect="postgres") for g in group_cols}
                col_sql = col_expr.sql(dialect="postgres")
                if col_sql not in group_sql:
                    group_cols.append(col_expr)
                    group_expr.set("expressions", group_cols)

        deduped = []
        for alias in hidden_aliases:
            if alias and alias not in deduped:
                deduped.append(alias)
        return expr.sql(dialect="postgres"), deduped

    def _ensure_hidden_prev_columns(
        self,
        sql: str,
        hidden_columns: List[str],
    ) -> str:
        if not sql or not hidden_columns:
            return sql
        try:
            expr = parse_one(sql, read="postgres")
        except Exception:
            return sql
        select_expr = self._outer_select(expr)
        if not select_expr:
            return sql
        from_expr = select_expr.args.get("from_")
        main_table = from_expr.this if from_expr else None
        main_alias = None
        if isinstance(main_table, exp.Table):
            main_alias = main_table.alias_or_name or main_table.name
        main_alias = main_alias or "prev"

        existing_aliases = {
            alias.alias_or_name.lower()
            for alias in select_expr.expressions
            if isinstance(alias, exp.Alias) and alias.alias_or_name
        }
        existing_columns = {
            col.name.lower()
            for col in select_expr.expressions
            if isinstance(col, exp.Column) and col.name
        }

        for col in select_expr.find_all(exp.Column):
            if col.name and col.name.lower().startswith(HIDDEN_PK_PREFIX) and not col.table:
                col.set("table", exp.Identifier(this=main_alias))

        for hidden in hidden_columns:
            if hidden.lower() in existing_aliases or hidden.lower() in existing_columns:
                continue
            select_expr.select(exp.column(hidden, table=main_alias), append=True)
            group_expr = select_expr.args.get("group")
            if group_expr:
                group_cols = list(group_expr.expressions)
                group_sql = {g.sql(dialect="postgres") for g in group_cols}
                col_sql = exp.column(hidden, table=main_alias).sql(dialect="postgres")
                if col_sql not in group_sql:
                    group_cols.append(exp.column(hidden, table=main_alias))
                    group_expr.set("expressions", group_cols)

        return expr.sql(dialect="postgres")

    def _infer_entity_context(
        self,
        sql: str,
        display_columns: List[str],
    ) -> Dict[str, Any]:
        base_relations = self.column_metadata.extract_table_names(sql)
        entity_table: Optional[str] = None
        primary_key: List[str] = []
        display_lower = {c.lower() for c in display_columns}

        for table in base_relations:
            pk = self.column_metadata.infer_primary_key(table)
            if not pk:
                continue
            table_cols = set(self.column_metadata.table_columns(table))
            if display_lower and table_cols.intersection(display_lower):
                entity_table = table
                primary_key = pk
                break

        if not entity_table:
            for table in base_relations:
                pk = self.column_metadata.infer_primary_key(table)
                if pk:
                    entity_table = table
                    primary_key = pk
                    break

        entity_type = self.column_metadata.infer_entity_type(entity_table) if entity_table else None

        return {
            "entity_table": entity_table,
            "entity_type": entity_type,
            "primary_key": primary_key,
            "base_relations": base_relations,
        }

    def _build_sql_signature(self, sql: str) -> Dict[str, Any]:
        signature: Dict[str, Any] = {"tables": self.column_metadata.extract_table_names(sql)}
        if not sql:
            return signature
        try:
            expr = parse_one(sql, read="postgres")
        except Exception:
            return signature

        joins: List[str] = []
        for join in expr.find_all(exp.Join):
            on_expr = join.args.get("on")
            if on_expr is not None:
                joins.append(on_expr.sql(dialect="postgres"))
        signature["joins"] = joins

        where_expr = expr.find(exp.Where)
        if where_expr and where_expr.this:
            signature["filters"] = where_expr.this.sql(dialect="postgres")

        group_expr = expr.find(exp.Group)
        if group_expr:
            signature["group_by"] = [g.sql(dialect="postgres") for g in group_expr.expressions]

        order_expr = expr.find(exp.Order)
        if order_expr:
            signature["order_by"] = [o.sql(dialect="postgres") for o in order_expr.expressions]

        select_expr = self._outer_select(expr)
        if select_expr:
            signature["select"] = [s.sql(dialect="postgres") for s in select_expr.expressions]

        return signature

    def _snapshot_display_columns(self, snapshot: Dict[str, Any]) -> List[str]:
        display, _ = self._split_snapshot_columns(snapshot)
        return display

    def _snapshot_hidden_columns(self, snapshot: Dict[str, Any]) -> List[str]:
        _, hidden = self._split_snapshot_columns(snapshot)
        return hidden

    def _can_refine_snapshot(
        self,
        snapshot: Dict[str, Any],
        strategy: str,
    ) -> Tuple[bool, str]:
        if strategy != "transform":
            return False, "Follow-up strategy requires rewrite."
        if not snapshot:
            return False, "Missing snapshot for refinement."
        entity_type = snapshot.get("entity_type")
        primary_key = snapshot.get("primary_key") or []
        hidden = [h.lower() for h in self._snapshot_hidden_columns(snapshot)]
        if not entity_type:
            return False, "Snapshot missing entity_type."
        if not primary_key:
            return False, "Snapshot missing primary_key."
        for pk in primary_key:
            expected = f"{HIDDEN_PK_PREFIX}{pk}"
            if expected.lower() not in hidden:
                return False, f"Snapshot missing hidden key column {expected}."
        return True, ""

    def _build_rewrite_fallback_question(
        self,
        question: str,
        snapshot: Dict[str, Any],
    ) -> str:
        last_sql = snapshot.get("sql") or ""
        last_summary = snapshot.get("summary") or ""
        base_relations = snapshot.get("base_relations") or []
        entity_type = snapshot.get("entity_type") or ""
        parts = [question.strip()]
        if last_sql:
            parts.append(f"Use the same base constraints as this prior SQL: {last_sql}")
        if last_summary:
            parts.append(f"Prior result summary: {last_summary}")
        if base_relations:
            parts.append(f"Base tables/views: {', '.join(base_relations)}")
        if entity_type:
            parts.append(f"Entity type: {entity_type}.")
        return "\n".join([p for p in parts if p])

    def _build_summary(
        self,
        row_count: int,
        columns: List[str],
        stats: Optional[Dict[str, Any]],
        sample_size: int,
        truncated: bool,
    ) -> str:
        if row_count == 0:
            return "No rows returned."

        parts: List[str] = [f"Returned {row_count} rows."]
        if columns:
            preview = ", ".join(columns[:6])
            if len(columns) > 6:
                preview += ", ..."
            parts.append(f"Columns: {preview}.")

        if stats and stats.get("columns"):
            col_profiles = stats["columns"]
            numeric_cols = [
                name
                for name, profile in col_profiles.items()
                if profile.get("type") == "number"
                and profile.get("min") is not None
                and profile.get("max") is not None
            ]
            if numeric_cols:
                name = numeric_cols[0]
                profile = col_profiles[name]
                parts.append(
                    f"{name} ranges from {profile['min']} to {profile['max']} (sample)."
                )
            else:
                categorical_cols = [
                    name
                    for name, profile in col_profiles.items()
                    if profile.get("top_values")
                ]
                if categorical_cols:
                    name = categorical_cols[0]
                    top = col_profiles[name]["top_values"][0]
                    parts.append(f"Top {name}: {top['value']} ({top['count']}).")

        if truncated:
            parts.append(f"Showing a {sample_size}-row sample.")

        return " ".join(parts)

    def _build_result_snapshot(
        self,
        result: ControllerResult,
        display_rows: Optional[List[Dict[str, Any]]] = None,
        hidden_columns: Optional[List[str]] = None,
        entity_context: Optional[Dict[str, Any]] = None,
        sql_signature: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        sample_size = RESULT_SAMPLE_SIZE
        rows = display_rows if display_rows is not None else (result.rows or [])
        rows_sample = rows[:sample_size]
        display_columns: List[str] = []
        seen = set()
        for row in rows_sample:
            for key in (row or {}).keys():
                if key not in seen:
                    seen.add(key)
                    display_columns.append(key)
        stats = self._profile_columns(rows_sample, display_columns)
        truncated = len(rows) > sample_size
        summary = self._build_summary(
            row_count=len(rows),
            columns=display_columns,
            stats=stats,
            sample_size=sample_size,
            truncated=truncated,
        )
        column_info = self.column_metadata.describe_columns(display_columns, result.sql)
        hidden = list(hidden_columns or [])
        columns = {"display": display_columns, "hidden": hidden}
        lineage = entity_context or {}
        signature = sql_signature if sql_signature is not None else self._build_sql_signature(result.sql or "")
        snapshot = {
            "sql": result.sql or "",
            "row_count": len(rows),
            "rows_sample": rows_sample,
            "columns": columns,
            "stats": stats,
            "chart": result.figure,
            "summary": summary,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "truncated": truncated,
            "column_info": column_info or None,
            "entity_type": lineage.get("entity_type"),
            "primary_key": lineage.get("primary_key") or [],
            "base_relations": lineage.get("base_relations") or [],
            "sql_signature": signature,
        }
        return snapshot

    def _build_response(self, **kwargs: Any) -> QueryResponse:
        return QueryResponse(**kwargs)

    def _is_snapshot_fresh(self, snapshot: Dict[str, Any]) -> bool:
        ttl_minutes = settings.followup_context_ttl_minutes
        if ttl_minutes <= 0:
            return True
        ttl_minutes = max(ttl_minutes, 1)
        ts = snapshot.get("timestamp")
        if not ts:
            return False
        try:
            ts_dt = datetime.fromisoformat(ts)
        except ValueError:
            return False
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - ts_dt
        return age <= timedelta(minutes=ttl_minutes)

    def _log_metrics(
        self,
        action: str,
        question: str,
        duration_ms: int,
        chat_id: Optional[str],
        session_id: Optional[str],
        usage: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        extra: Dict[str, Any] = {"duration_ms": duration_ms}
        if usage:
            extra["usage"] = usage
        if error:
            extra["error"] = error
        log_audit_event(
            event_type="metrics",
            action=action,
            question=question,
            chat_id=chat_id,
            session_id=session_id,
            extra=extra,
        )

    def _append_followup_failure(
        self,
        chat_id: Optional[str],
        question: str,
        assistant_text: str,
        action: str,
        reason: Optional[str] = None,
        source_message_id: Optional[str] = None,
        tables_override: Optional[List[str]] = None,
        execute: bool = True,
        plan_only: bool = False,
    ) -> None:
        if not chat_id:
            return
        try:
            append_message(
                chat_id=chat_id,
                role="user",
                content=question,
                metadata={
                    "execute": execute,
                    "plan_only": plan_only,
                    "tables_override": tables_override or [],
                },
            )
            append_message(
                chat_id=chat_id,
                role="assistant",
                content=assistant_text,
                metadata={
                    "kind": "followup_failed",
                    "action": action,
                    "reason": reason,
                    "source_message_id": source_message_id,
                    "fallback_question": question,
                },
            )
        except Exception as exc:
            logger.warning("Failed to persist follow-up failure message: %s", exc)

    def _coerce_refinement_sql(
        self,
        refined_sql: str,
        previous_sql: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        if not refined_sql:
            return None, "Refinement returned empty SQL."

        prev_sql = (previous_sql or "").strip().rstrip(";")
        if not prev_sql:
            return None, "Missing previous SQL for refinement."

        try:
            expr = parse_one(refined_sql, read="postgres")
        except Exception as exc:
            return None, f"Could not parse refined SQL: {exc}"

        outer_tables = [
            table
            for table in expr.find_all(exp.Table)
            if not table.find_ancestor(exp.CTE)
        ]
        if not outer_tables:
            return None, "Refinement must select from the previous result."
        if len(outer_tables) > 1:
            return None, "Refinement cannot join multiple tables; it must use prev only."

        prev_alias = None
        for table in outer_tables:
            if prev_alias is None:
                prev_alias = table.alias_or_name or table.name
            table.set("this", exp.Identifier(this="prev"))
            table.set("db", None)
            table.set("catalog", None)
        if prev_alias and prev_alias.lower() != "prev":
            for col in expr.find_all(exp.Column):
                if (col.table or "").lower() == prev_alias.lower():
                    col.set("table", exp.Identifier(this="prev"))
            for table in outer_tables:
                table.set("alias", None)

        expr.set("with_", None)
        main_sql = expr.sql(dialect="postgres")
        return f"WITH prev AS ({prev_sql}) {main_sql}", None

    def _coerce_enrichment_sql(
        self,
        refined_sql: str,
        previous_sql: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        if not refined_sql:
            return None, "Enrichment returned empty SQL."

        prev_sql = (previous_sql or "").strip().rstrip(";")
        if not prev_sql:
            return None, "Missing previous SQL for enrichment."

        try:
            expr = parse_one(refined_sql, read="postgres")
        except Exception as exc:
            return None, f"Could not parse enrichment SQL: {exc}"

        from_expr = expr.args.get("from_")
        if not from_expr or not isinstance(from_expr.this, exp.Table):
            return None, "Enrichment must select from prev."

        main_table = from_expr.this
        main_alias = main_table.alias_or_name or main_table.name

        if main_table.name.lower() != "prev":
            main_table.set("this", exp.Identifier(this="prev"))
            main_table.set("db", None)
            main_table.set("catalog", None)
        if main_alias and main_alias.lower() != "prev":
            for col in expr.find_all(exp.Column):
                if (col.table or "").lower() == main_alias.lower():
                    col.set("table", exp.Identifier(this="prev"))
            main_table.set("alias", None)

        cte_names = {
            cte.alias_or_name.lower()
            for cte in expr.find_all(exp.CTE)
            if cte.alias_or_name
        }

        for table in expr.find_all(exp.Table):
            if table.find_ancestor(exp.CTE):
                continue
            alias = table.alias_or_name or table.name
            if alias and alias == main_alias:
                continue
            if table.name.lower() in {"prev"}:
                continue
            if table.name.lower() in cte_names:
                continue
            join = table.find_ancestor(exp.Join)
            if not join:
                return None, "All additional tables must be joined to prev."
            on_expr = join.args.get("on")
            if not on_expr:
                return None, "Joined tables must include an ON condition."
            columns = list(on_expr.find_all(exp.Column))
            has_prev = any(
                (col.table or "").lower() in {str(main_alias).lower(), "prev"}
                for col in columns
            )
            has_other = any(
                (col.table or "").lower() == (alias or "").lower()
                for col in columns
            )
            if not (has_prev and has_other):
                return None, "Join condition must reference prev and the joined table."
            for col in columns:
                if (col.table or "").lower() in {str(main_alias).lower(), "prev"}:
                    if col.name and not col.name.lower().startswith(HIDDEN_PK_PREFIX):
                        return None, "Join must use hidden identity columns from prev."

        expr.set("with_", None)
        main_sql = expr.sql(dialect="postgres")
        return f"WITH prev AS ({prev_sql}) {main_sql}", None

    def interpret_result(
        self,
        question: str,
        chat_id: str,
        message_id: Optional[str] = None,
        session_id: Optional[str] = None,
        pinned_context: bool = False,
    ) -> Dict[str, Any]:
        memory = get_result_snapshot(chat_id, message_id) if message_id else None
        if memory is None:
            memory = get_last_result_snapshot(chat_id)
        if memory is None:
            raise ValueError("No prior result found to explain.")

        interpretation = self.interpreter.interpret(question, memory.snapshot)
        tables = self.column_metadata.extract_table_names(memory.snapshot.get("sql") or "")
        log_audit_event(
            event_type="interpret",
            action="interpret",
            question=question,
            chat_id=chat_id,
            session_id=session_id,
            sql=memory.snapshot.get("sql"),
            tables=tables,
            row_count=memory.snapshot.get("row_count"),
            extra={
                "source_message_id": memory.message_id,
                "pinned_context": pinned_context,
            },
        )

        append_message(
            chat_id=chat_id,
            role="user",
            content=question,
            metadata={
                "kind": "interpretation_question",
                "source_message_id": memory.message_id,
            },
        )
        append_message(
            chat_id=chat_id,
            role="assistant",
            content=interpretation.text,
            metadata={
                "kind": "interpretation",
                "source_message_id": memory.message_id,
                "result_snapshot": memory.snapshot,
                "usage": interpretation.usage,
            },
        )

        return {
            "text": interpretation.text,
            "source_message_id": memory.message_id,
            "usage": interpretation.usage,
        }

    def _refine_from_snapshot(
        self,
        question: str,
        chat_id: str,
        memory_snapshot: Dict[str, Any],
        execute: bool = True,
        plan_only: bool = False,
        session_id: Optional[str] = None,
        source_message_id: Optional[str] = None,
        allow_joins: bool = False,
        refine_question: Optional[str] = None,
        action_override: str = "refine",
        extra_metadata: Optional[Dict[str, Any]] = None,
        audit_extra: Optional[Dict[str, Any]] = None,
    ) -> QueryResponse:
        previous_sql = memory_snapshot.get("sql") or ""
        display_columns, hidden_columns = self._split_snapshot_columns(memory_snapshot)
        available_columns = display_columns + hidden_columns
        if not available_columns:
            raise ValueError("No columns available to refine the previous result.")
        effective_question = refine_question or question
        refined = self.sql_refiner.refine(
            effective_question,
            previous_sql,
            available_columns,
            hidden_columns=hidden_columns,
            allow_joins=allow_joins,
        )
        usage: Dict[str, Any] = {}
        if refined.usage:
            usage["sql_refiner"] = refined.usage
        if refined.sql.strip().upper().startswith("REFINE_NOT_POSSIBLE"):
            raise ValueError(refined.sql.strip())
        if allow_joins:
            coerced_sql, reason = self._coerce_enrichment_sql(refined.sql, previous_sql)
        else:
            coerced_sql, reason = self._coerce_refinement_sql(refined.sql, previous_sql)
        if reason:
            logger.warning("Refinement SQL rejected: %s", reason)
            refined = self.sql_refiner.refine(
                effective_question,
                previous_sql,
                available_columns,
                hidden_columns=hidden_columns,
                strict=True,
                allow_joins=allow_joins,
            )
            if refined.usage:
                usage["sql_refiner_strict"] = refined.usage
            if refined.sql.strip().upper().startswith("REFINE_NOT_POSSIBLE"):
                raise ValueError(refined.sql.strip())
            if allow_joins:
                coerced_sql, reason = self._coerce_enrichment_sql(refined.sql, previous_sql)
            else:
                coerced_sql, reason = self._coerce_refinement_sql(refined.sql, previous_sql)
            if reason:
                raise ValueError(f"Refinement invalid: {reason}")

        refined_sql = self._ensure_hidden_prev_columns(coerced_sql or refined.sql, hidden_columns)

        allowed_columns = available_columns
        if allow_joins:
            allowed_columns = sorted(set(available_columns).union(self.kb.allowed_columns()))

        validator = SQLValidator(
            allowed_objects=self.kb.allowed_objects(),
            allowed_columns=allowed_columns,
            ignore_cte_columns=True,
        )
        validated_sql, notes = validator.validate(refined_sql)

        rows: List[Dict[str, Any]] = []
        if execute and not plan_only:
            rows = execute_readonly_query(
                validated_sql,
                allowed_objects=self.kb.allowed_objects(),
                allowed_columns=allowed_columns,
            )
        tables = self.column_metadata.extract_table_names(validated_sql)
        audit_metadata = {
            "source_sql": previous_sql,
            "source_message_id": source_message_id,
            "allow_joins": allow_joins,
        }
        if effective_question != question:
            audit_metadata["effective_question"] = effective_question
        if audit_extra:
            audit_metadata.update(audit_extra)
        log_audit_event(
            event_type="refine",
            action=action_override,
            question=question,
            chat_id=chat_id,
            session_id=session_id,
            sql=validated_sql,
            tables=tables,
            row_count=len(rows),
            extra=audit_metadata or None,
        )

        display_rows = self._strip_hidden_columns(rows)
        entity_context = {
            "entity_type": memory_snapshot.get("entity_type"),
            "primary_key": memory_snapshot.get("primary_key") or [],
            "base_relations": memory_snapshot.get("base_relations") or [],
        }
        if not entity_context["entity_type"]:
            inferred = self._infer_entity_context(validated_sql, display_columns)
            if inferred.get("entity_type"):
                entity_context.update(inferred)
        sql_signature = self._build_sql_signature(validated_sql)
        result_snapshot = self._build_result_snapshot(
            SimpleNamespace(sql=validated_sql, rows=rows, figure=None),
            display_rows=display_rows,
            hidden_columns=hidden_columns,
            entity_context=entity_context,
            sql_signature=sql_signature,
        )

        message_metadata = dict(extra_metadata or {})
        if effective_question != question:
            message_metadata.setdefault("effective_question", effective_question)

        append_message(
            chat_id=chat_id,
            role="user",
            content=question,
            metadata={
                "kind": "followup_refine",
                "source_sql": previous_sql,
                "source_message_id": source_message_id,
                "allow_joins": allow_joins,
                **message_metadata,
            },
        )
        append_message(
            chat_id=chat_id,
            role="assistant",
            content=validated_sql or "Result",
            metadata={
                "kind": "followup_refine",
                "sql": validated_sql,
                "rows": display_rows,
                "figure": None,
                "intent": "retrieval",
                "suggested_tables": [],
                "trace": [],
                "plan": [],
                "duration_ms": None,
                "notes": notes,
                "result_snapshot": result_snapshot,
                "source_message_id": source_message_id,
                "allow_joins": allow_joins,
                **message_metadata,
            },
        )

        return QueryResponse(
            sql=validated_sql,
            rows=display_rows,
            figure=None,
            intent="retrieval",
            suggested_tables=[],
            trace=[],
            plan=[],
            duration_ms=None,
            notes=notes,
            result_snapshot=result_snapshot,
            action=action_override,
            usage=usage or None,
        )

    def _run_new_query(
        self,
        question: str,
        effective_question: Optional[str],
        execute: bool,
        plan_only: bool,
        tables_override: Optional[List[str]],
        chat_id: Optional[str],
        session_id: Optional[str],
        action_override: str,
        start_time: Optional[float] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
        audit_extra: Optional[Dict[str, Any]] = None,
        usage_prefix: Optional[Dict[str, Any]] = None,
    ) -> QueryResponse:
        start = start_time or time.perf_counter()
        effective = effective_question or question
        decision = self.router.route(effective)

        if decision.agent not in {"viz_agent", "sql_agent"}:
            raise ValueError(
                "Sorry, no agent is available to handle this question. "
                "Please try rephrasing or ask a data or visualization question."
            )

        suggested_tables = tables_override or self.table_agent.suggest(effective)
        tables_for_controller = tables_override if tables_override else None

        if decision.usage:
            logger.info("Intent classifier usage: %s", decision.usage)
        if suggested_tables:
            logger.info("Suggested tables for question '%s': %s", effective, suggested_tables)

        controller_result: ControllerResult = self.controller.run(
            question=effective,
            intent=decision.intent,
            execute=execute,
            plan_only=plan_only,
            tables_override=tables_for_controller,
        )
        display_rows = self._strip_hidden_columns(controller_result.rows or [])
        display_columns: List[str] = []
        seen = set()
        for row in display_rows[:RESULT_SAMPLE_SIZE]:
            for key in (row or {}).keys():
                if key not in seen:
                    seen.add(key)
                    display_columns.append(key)
        entity_context = self._infer_entity_context(controller_result.sql, display_columns)
        effective_sql, hidden_columns = self._ensure_hidden_pk_columns(
            controller_result.sql,
            entity_context.get("entity_table"),
            entity_context.get("primary_key") or [],
        )
        sql_signature = self._build_sql_signature(effective_sql)
        result_snapshot = self._build_result_snapshot(
            SimpleNamespace(sql=effective_sql, rows=controller_result.rows, figure=controller_result.figure),
            display_rows=display_rows,
            hidden_columns=hidden_columns,
            entity_context=entity_context,
            sql_signature=sql_signature,
        )
        tables = self.column_metadata.extract_table_names(effective_sql)
        extra: Dict[str, Any] = {}
        if effective != question:
            extra["effective_question"] = effective
        if audit_extra:
            extra.update(audit_extra)
        log_audit_event(
            event_type="query",
            action=action_override,
            question=question,
            chat_id=chat_id,
            session_id=session_id,
            sql=effective_sql,
            tables=tables,
            row_count=len(controller_result.rows or []),
            extra=extra or None,
        )

        duration_ms = int((time.perf_counter() - start) * 1000)
        usage: Dict[str, Any] = dict(usage_prefix or {})
        if decision.usage:
            usage["intent_classifier"] = decision.usage
        if controller_result.usage:
            usage.update(controller_result.usage)
        self._log_metrics(
            action=action_override,
            question=question,
            duration_ms=duration_ms,
            chat_id=chat_id,
            session_id=session_id,
            usage=usage or None,
        )

        if chat_id:
            try:
                user_meta = {
                    "execute": execute,
                    "plan_only": plan_only,
                    "tables_override": tables_override or [],
                }
                assistant_meta = {
                    "sql": effective_sql,
                    "rows": display_rows,
                    "figure": controller_result.figure,
                    "intent": controller_result.intent,
                    "suggested_tables": suggested_tables,
                    "trace": controller_result.trace,
                    "plan": controller_result.plan,
                    "duration_ms": duration_ms,
                    "notes": controller_result.notes,
                    "result_snapshot": result_snapshot,
                }
                if effective != question:
                    user_meta["effective_question"] = effective
                    assistant_meta["effective_question"] = effective
                if extra_metadata:
                    user_meta.update(extra_metadata)
                    assistant_meta.update(extra_metadata)
                append_message(
                    chat_id=chat_id,
                    role="user",
                    content=question,
                    metadata=user_meta,
                )
                maybe_autotitle_chat(chat_id, question)
                append_message(
                    chat_id=chat_id,
                    role="assistant",
                    content=effective_sql or "Result",
                    metadata=assistant_meta,
                )
            except Exception as exc:
                logger.warning("Failed to persist chat messages: %s", exc)

        return QueryResponse(
            sql=effective_sql,
            rows=display_rows,
            figure=controller_result.figure,
            intent=controller_result.intent,
            suggested_tables=suggested_tables,
            trace=controller_result.trace,
            plan=controller_result.plan,
            duration_ms=duration_ms,
            notes=controller_result.notes,
            result_snapshot=result_snapshot,
            action=action_override,
            usage=usage or None,
        )


    def handle_question(
        self,
        question: str,
        execute: bool = True,
        plan_only: bool = False,
        tables_override: Optional[List[str]] = None,
        chat_id: Optional[str] = None,
        session_id: Optional[str] = None,
        force_new: bool = False,
        context_message_id: Optional[str] = None,
    ) -> QueryResponse:
        return self.orchestrator.run(
            question=question,
            execute=execute,
            plan_only=plan_only,
            tables_override=tables_override,
            chat_id=chat_id,
            session_id=session_id,
            force_new=force_new,
            context_message_id=context_message_id,
        )
