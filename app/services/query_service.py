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
from app.agents.followup_resolver import FollowupResolver
from app.config import settings
from app.services.agent_controller import AgentController, ControllerResult
from app.services.audit_logger import log_audit_event
from app.services.chat_store import append_message, maybe_autotitle_chat
from app.services.result_memory import get_last_result_snapshot, get_result_snapshot
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
        self.followup_resolver = FollowupResolver()
        self.column_metadata = ColumnMetadataStore(settings.metadata_path)
        self.cache: Optional[Dict[str, List[Dict[str, Any]]]] = {} if settings.enable_query_cache else None
        self.controller = AgentController(
            kb=self.kb,
            vector_store=self.vector_store,
            sql_agent=self.sql_agent,
            viz_agent=self.viz_agent,
            cache=self.cache,
        )

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

    def _build_result_snapshot(self, result: ControllerResult) -> Dict[str, Any]:
        sample_size = RESULT_SAMPLE_SIZE
        rows = result.rows or []
        rows_sample = rows[:sample_size]
        columns: List[str] = []
        seen = set()
        for row in rows_sample:
            for key in (row or {}).keys():
                if key not in seen:
                    seen.add(key)
                    columns.append(key)
        stats = self._profile_columns(rows_sample, columns)
        truncated = len(rows) > sample_size
        summary = self._build_summary(
            row_count=len(rows),
            columns=columns,
            stats=stats,
            sample_size=sample_size,
            truncated=truncated,
        )
        column_info = self.column_metadata.describe_columns(columns, result.sql)
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
        }
        return snapshot

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

        for table in outer_tables:
            table.set("this", exp.Identifier(this="prev"))
            table.set("db", None)
            table.set("catalog", None)

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
    ) -> QueryResponse:
        previous_sql = memory_snapshot.get("sql") or ""
        available_columns = memory_snapshot.get("columns") or []
        if not available_columns:
            raise ValueError("No columns available to refine the previous result.")
        refined = self.sql_refiner.refine(question, previous_sql, available_columns)
        usage: Dict[str, Any] = {}
        if refined.usage:
            usage["sql_refiner"] = refined.usage
        coerced_sql, reason = self._coerce_refinement_sql(refined.sql, previous_sql)
        if reason:
            logger.warning("Refinement SQL rejected: %s", reason)
            refined = self.sql_refiner.refine(
                question,
                previous_sql,
                available_columns,
                strict=True,
            )
            if refined.usage:
                usage["sql_refiner_strict"] = refined.usage
            coerced_sql, reason = self._coerce_refinement_sql(refined.sql, previous_sql)
            if reason:
                raise ValueError(f"Refinement invalid: {reason}")

        validator = SQLValidator(
            allowed_objects=self.kb.allowed_objects(),
            allowed_columns=available_columns,
            ignore_cte_columns=True,
        )
        validated_sql, notes = validator.validate(coerced_sql or refined.sql)

        rows: List[Dict[str, Any]] = []
        if execute and not plan_only:
            rows = execute_readonly_query(
                validated_sql,
                allowed_objects=self.kb.allowed_objects(),
                allowed_columns=available_columns,
            )
        tables = self.column_metadata.extract_table_names(validated_sql)
        log_audit_event(
            event_type="refine",
            action="refine",
            question=question,
            chat_id=chat_id,
            session_id=session_id,
            sql=validated_sql,
            tables=tables,
            row_count=len(rows),
            extra={
                "source_sql": previous_sql,
                "source_message_id": source_message_id,
            },
        )

        result_snapshot = self._build_result_snapshot(
            SimpleNamespace(sql=validated_sql, rows=rows, figure=None)
        )

        append_message(
            chat_id=chat_id,
            role="user",
            content=question,
            metadata={
                "kind": "followup_refine",
                "source_sql": previous_sql,
                "source_message_id": source_message_id,
            },
        )
        append_message(
            chat_id=chat_id,
            role="assistant",
            content=validated_sql or "Result",
            metadata={
                "kind": "followup_refine",
                "sql": validated_sql,
                "rows": rows,
                "figure": None,
                "intent": "retrieval",
                "suggested_tables": [],
                "trace": [],
                "plan": [],
                "duration_ms": None,
                "notes": notes,
                "result_snapshot": result_snapshot,
                "source_message_id": source_message_id,
            },
        )

        return QueryResponse(
            sql=validated_sql,
            rows=rows,
            figure=None,
            intent="retrieval",
            suggested_tables=[],
            trace=[],
            plan=[],
            duration_ms=None,
            notes=notes,
            result_snapshot=result_snapshot,
            action="refine",
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
        start_time = time.perf_counter()
        if chat_id and not force_new:
            memory = None
            pinned_context = False
            if context_message_id:
                memory = get_result_snapshot(chat_id, context_message_id)
                if memory:
                    pinned_context = True
                else:
                    logger.info(
                        "Pinned context not found; handling as new query (chat_id=%s, message_id=%s)",
                        chat_id,
                        context_message_id,
                    )
                    assistant_text = (
                        "That loaded result is no longer available. "
                        "Please load another result or run this as a new query."
                    )
                    self._append_followup_failure(
                        chat_id=chat_id,
                        question=question,
                        assistant_text=assistant_text,
                        action="context_missing",
                        reason="Pinned context not found",
                        source_message_id=context_message_id,
                        tables_override=tables_override,
                        execute=execute,
                        plan_only=plan_only,
                    )
                    duration_ms = int((time.perf_counter() - start_time) * 1000)
                    self._log_metrics(
                        action="context_missing",
                        question=question,
                        duration_ms=duration_ms,
                        chat_id=chat_id,
                        session_id=session_id,
                        usage={"pinned_context": {"message_id": context_message_id}},
                        error="Pinned context not found",
                    )
                    return QueryResponse(
                        sql="",
                        rows=[],
                        figure=None,
                        intent="retrieval",
                        suggested_tables=[],
                        trace=[],
                        plan=[],
                        duration_ms=duration_ms,
                        notes=["Pinned context not found"],
                        result_snapshot=None,
                        action="context_missing",
                        assistant_text=assistant_text,
                        fallback_question=question,
                    )
            if memory is None:
                memory = get_last_result_snapshot(chat_id)
            if memory:
                if not pinned_context and not self._is_snapshot_fresh(memory.snapshot):
                    logger.info(
                        "Follow-up context is stale; handling as new query (chat_id=%s)",
                        chat_id,
                    )
                else:
                    logger.info(
                        "Follow-up context found (chat_id=%s, message_id=%s)",
                        chat_id,
                        memory.message_id,
                    )
                    last_summary = memory.snapshot.get("summary") or ""
                    last_sql = memory.snapshot.get("sql") or ""
                    decision = self.followup_resolver.resolve(question, last_summary, last_sql)
                    logger.info(
                        "Follow-up resolver decision: %s (reason=%s)",
                        decision.action,
                        decision.reason or "",
                    )
                    if decision.action == "interpret":
                        logger.info("Routing to interpretation for follow-up.")
                        try:
                            interpretation = self.interpret_result(
                                question,
                                chat_id,
                                message_id=memory.message_id,
                                session_id=session_id,
                            )
                            duration_ms = int((time.perf_counter() - start_time) * 1000)
                            usage = {}
                            if decision.usage:
                                usage["followup_resolver"] = decision.usage
                            if interpretation.get("usage"):
                                usage["interpreter"] = interpretation["usage"]
                            if pinned_context:
                                usage["pinned_context"] = {"message_id": memory.message_id}
                            self._log_metrics(
                                action="interpret",
                                question=question,
                                duration_ms=duration_ms,
                                chat_id=chat_id,
                                session_id=session_id,
                                usage=usage or None,
                            )
                            return QueryResponse(
                                sql="",
                                rows=[],
                                figure=None,
                                intent="interpretation",
                                suggested_tables=[],
                                trace=[],
                                plan=[],
                                duration_ms=duration_ms,
                                notes=[],
                                result_snapshot=memory.snapshot,
                                action="interpret",
                                assistant_text=interpretation.get("text"),
                                usage=usage or None,
                            )
                        except Exception as exc:
                            reason = str(exc)
                            assistant_text = (
                                "I couldn't interpret the previous result. "
                                "You can run this as a new query instead."
                            )
                            self._append_followup_failure(
                                chat_id=chat_id,
                                question=question,
                                assistant_text=assistant_text,
                                action="interpret_failed",
                                reason=reason,
                                source_message_id=memory.message_id,
                                tables_override=tables_override,
                                execute=execute,
                                plan_only=plan_only,
                            )
                            duration_ms = int((time.perf_counter() - start_time) * 1000)
                            usage = {"followup_resolver": decision.usage} if decision.usage else None
                            if pinned_context:
                                usage = usage or {}
                                usage["pinned_context"] = {"message_id": memory.message_id}
                            self._log_metrics(
                                action="interpret_failed",
                                question=question,
                                duration_ms=duration_ms,
                                chat_id=chat_id,
                                session_id=session_id,
                                usage=usage,
                                error=reason,
                            )
                            return QueryResponse(
                                sql="",
                                rows=[],
                                figure=None,
                                intent="interpretation",
                                suggested_tables=[],
                                trace=[],
                                plan=[],
                                duration_ms=duration_ms,
                                notes=[reason] if reason else [],
                                result_snapshot=memory.snapshot,
                                action="interpret_failed",
                                assistant_text=assistant_text,
                                fallback_question=question,
                                usage=usage,
                            )
                    if decision.action == "refine":
                        logger.info("Routing to refinement for follow-up.")
                        try:
                            result = self._refine_from_snapshot(
                                question,
                                chat_id=chat_id,
                                memory_snapshot=memory.snapshot,
                                execute=execute,
                                plan_only=plan_only,
                                session_id=session_id,
                                source_message_id=memory.message_id,
                            )
                            result.action = "refine"
                            duration_ms = int((time.perf_counter() - start_time) * 1000)
                            result.duration_ms = duration_ms
                            usage = {}
                            if decision.usage:
                                usage["followup_resolver"] = decision.usage
                            if result.usage:
                                usage.update(result.usage)
                            if pinned_context:
                                usage["pinned_context"] = {"message_id": memory.message_id}
                            self._log_metrics(
                                action="refine",
                                question=question,
                                duration_ms=duration_ms,
                                chat_id=chat_id,
                                session_id=session_id,
                                usage=usage or None,
                            )
                            result.usage = usage or None
                            return result
                        except Exception as exc:
                            reason = str(exc)
                            logger.warning("Refinement failed: %s", exc)
                            assistant_text = (
                                "I couldn't safely refine the previous result. "
                                "You can run this as a new query instead."
                            )
                            self._append_followup_failure(
                                chat_id=chat_id,
                                question=question,
                                assistant_text=assistant_text,
                                action="refine_failed",
                                reason=reason,
                                source_message_id=memory.message_id,
                                tables_override=tables_override,
                                execute=execute,
                                plan_only=plan_only,
                            )
                            duration_ms = int((time.perf_counter() - start_time) * 1000)
                            usage = {"followup_resolver": decision.usage} if decision.usage else None
                            if pinned_context:
                                usage = usage or {}
                                usage["pinned_context"] = {"message_id": memory.message_id}
                            self._log_metrics(
                                action="refine_failed",
                                question=question,
                                duration_ms=duration_ms,
                                chat_id=chat_id,
                                session_id=session_id,
                                usage=usage,
                                error=reason,
                            )
                            return QueryResponse(
                                sql="",
                                rows=[],
                                figure=None,
                                intent="retrieval",
                                suggested_tables=[],
                                trace=[],
                                plan=[],
                                duration_ms=duration_ms,
                                notes=[reason] if reason else [],
                                result_snapshot=memory.snapshot,
                                action="refine_failed",
                                assistant_text=assistant_text,
                                fallback_question=question,
                                usage=usage,
                            )
            else:
                logger.info("No follow-up context found; handling as new query.")

        decision = self.router.route(question)

        if decision.agent not in {"viz_agent", "sql_agent"}:
            raise ValueError(
                "Sorry, no agent is available to handle this question. "
                "Please try rephrasing or ask a data or visualization question."
            )

        suggested_tables = tables_override or self.table_agent.suggest(question)
        tables_for_controller = tables_override if tables_override else None

        if decision.usage:
            logger.info("Intent classifier usage: %s", decision.usage)
        if suggested_tables:
            logger.info("Suggested tables for question '%s': %s", question, suggested_tables)

        controller_result: ControllerResult = self.controller.run(
            question=question,
            intent=decision.intent,
            execute=execute,
            plan_only=plan_only,
            tables_override=tables_for_controller,
        )
        result_snapshot = self._build_result_snapshot(controller_result)
        tables = self.column_metadata.extract_table_names(controller_result.sql)
        log_audit_event(
            event_type="query",
            action="new",
            question=question,
            chat_id=chat_id,
            session_id=session_id,
            sql=controller_result.sql,
            tables=tables,
            row_count=len(controller_result.rows or []),
        )

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        usage = {}
        if decision.usage:
            usage["intent_classifier"] = decision.usage
        if controller_result.usage:
            usage.update(controller_result.usage)
        self._log_metrics(
            action="new",
            question=question,
            duration_ms=duration_ms,
            chat_id=chat_id,
            session_id=session_id,
            usage=usage or None,
        )

        if chat_id:
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
                maybe_autotitle_chat(chat_id, question)
                append_message(
                    chat_id=chat_id,
                    role="assistant",
                    content=controller_result.sql or "Result",
                    metadata={
                        "sql": controller_result.sql,
                        "rows": controller_result.rows,
                        "figure": controller_result.figure,
                        "intent": controller_result.intent,
                        "suggested_tables": suggested_tables,
                        "trace": controller_result.trace,
                        "plan": controller_result.plan,
                        "duration_ms": duration_ms,
                        "notes": controller_result.notes,
                        "result_snapshot": result_snapshot,
                    },
                )
            except Exception as exc:
                logger.warning("Failed to persist chat messages: %s", exc)

        return QueryResponse(
            sql=controller_result.sql,
            rows=controller_result.rows,
            figure=controller_result.figure,
            intent=controller_result.intent,
            suggested_tables=suggested_tables,
            trace=controller_result.trace,
            plan=controller_result.plan,
            duration_ms=duration_ms,
            notes=controller_result.notes,
            result_snapshot=result_snapshot,
            action="new",
            usage=usage or None,
        )
