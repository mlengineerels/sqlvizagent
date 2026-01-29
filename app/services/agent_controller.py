from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.agents.planner import PlannerAgent, PlanStep
from app.agents.sql_agent import SQLAgent
from app.agents.sql_validator import SQLValidator
from app.agents.viz_agent import VizAgent
from app.agents.knowledge_base import KnowledgeBase
from app.db import execute_readonly_query
from app.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class TraceEvent:
    step: str
    tool: str
    status: str
    detail: str
    data: Optional[Any] = None


@dataclass
class ControllerResult:
    sql: str
    rows: List[Dict[str, Any]]
    figure: Optional[Dict[str, Any]]
    intent: str
    trace: List[Dict[str, Any]]
    plan: List[Dict[str, Any]]
    duration_ms: int
    notes: Optional[List[str]] = None
    usage: Optional[Dict[str, Any]] = None


class AgentController:
    """
    Orchestrates Plan -> Act -> Observe cycles using a planner and concrete tools.
    """

    def __init__(
        self,
        kb: KnowledgeBase,
        vector_store: Optional[VectorStore],
        sql_agent: SQLAgent,
        viz_agent: VizAgent,
        cache: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> None:
        self.kb = kb
        self.vector_store = vector_store
        self.sql_agent = sql_agent
        self.viz_agent = viz_agent
        self.planner = PlannerAgent()
        self.cache = cache

    def _execute_with_cache(self, sql: str, allowed_objects: Optional[List[str]]) -> List[Dict[str, Any]]:
        if self.cache is not None and sql in self.cache:
            return self.cache[sql]
        rows = execute_readonly_query(
            sql,
            allowed_objects=allowed_objects or self.kb.allowed_objects(),
            allowed_columns=self.kb.allowed_columns(),
        )
        if self.cache is not None:
            self.cache[sql] = rows
        return rows

    def _run_tool(
        self,
        tool: str,
        question: str,
        intent: str,
        execute: bool,
        plan_only: bool,
        state: Dict[str, Any],
        validator: SQLValidator,
    ) -> Dict[str, Any]:
        if tool == "retrieve_schema":
            schema = ""
            if self.vector_store:
                schema = self.vector_store.get_relevant_schema(question, top_k=2)
            state["schema_context"] = schema
            detail = "Retrieved schema context" if schema else "No schema retrieved (empty vector store?)"
            return {"detail": detail, "data": schema}

        if tool == "draft_sql":
            allowed = state.get("allowed_objects") or self.kb.allowed_objects()
            res = self.sql_agent.generate_sql(
                question,
                allowed_objects=allowed,
                schema_override=state.get("schema_context"),
            )
            state["sql"] = res.sql
            if res.usage:
                state.setdefault("usage", {})["sql_agent"] = res.usage
            return {"detail": "Drafted SQL", "data": res.sql}

        if tool == "plan_viz":
            plan = self.viz_agent.plan_viz(question)
            state["sql"] = plan.sql
            state["chart"] = plan.chart
            return {"detail": "Planned visualization", "data": {"sql": plan.sql, "chart": plan.chart}}

        if tool == "validate_sql":
            sql = state.get("sql")
            if not sql:
                raise ValueError("No SQL to validate.")
            validated, notes = validator.validate(sql)
            state["sql"] = validated
            state.setdefault("notes", []).extend(notes)
            return {"detail": "Validated SQL", "data": {"sql": validated, "notes": notes}}

        if tool == "execute_sql":
            if plan_only or not execute:
                state["rows"] = []
                return {"detail": "Execution skipped (plan-only or execute flag false)", "data": None}
            sql = state.get("sql")
            if not sql:
                raise ValueError("No SQL to execute.")
            rows = self._execute_with_cache(sql, state.get("allowed_objects"))
            state["rows"] = rows
            return {"detail": f"Executed SQL, rows={len(rows)}", "data": {"row_count": len(rows)}}

        if tool == "render_viz":
            if plan_only or not execute:
                return {"detail": "Rendering skipped (plan-only or execute flag false)", "data": None}
            chart = state.get("chart")
            rows = state.get("rows")
            if chart is None:
                raise ValueError("No chart spec available to render.")
            if rows is None:
                raise ValueError("No rows available to render.")
            figure = self.viz_agent._build_figure(rows, chart)
            state["figure"] = figure
            return {"detail": "Rendered visualization", "data": {"row_count": len(rows)}}

        if tool == "summarize":
            return {"detail": "Summary complete", "data": {"intent": intent}}

        raise ValueError(f"Unknown tool: {tool}")

    def run(
        self,
        question: str,
        intent: str,
        execute: bool = True,
        plan_only: bool = False,
        tables_override: Optional[List[str]] = None,
    ) -> ControllerResult:
        start = time.perf_counter()
        plan_steps: List[PlanStep] = self.planner.plan(question, intent)
        trace: List[TraceEvent] = []
        state: Dict[str, Any] = {"intent": intent, "question": question}
        allowed_objects = tables_override or self.kb.allowed_objects()
        state["allowed_objects"] = allowed_objects
        validator = SQLValidator(
            allowed_objects=allowed_objects,
            allowed_columns=self.kb.allowed_columns(),
        )

        for plan_step in plan_steps:
            event = TraceEvent(
                step=plan_step.step,
                tool=plan_step.tool,
                status="planned",
                detail=plan_step.description or "",
            )
            trace.append(event)
            try:
                result = self._run_tool(
                    plan_step.tool,
                    question,
                    intent,
                    execute,
                    plan_only,
                    state,
                    validator,
                )
                event.status = "success"
                event.detail = result.get("detail", event.detail)
                event.data = result.get("data")
            except Exception as exc:
                event.status = "error"
                event.detail = str(exc)
                # Attempt a single repair on DB execution errors.
                if plan_step.tool == "execute_sql" and "sql" in state:
                    try:
                        repair_event = TraceEvent(
                            step=f"{plan_step.step}_repair",
                            tool="repair_sql",
                            status="planned",
                            detail="Attempting repair after execution failure",
                        )
                        trace.append(repair_event)
                        repaired = self.sql_agent.repair_sql(question, state["sql"], str(exc))
                        repair_event.status = "success"
                        repair_event.detail = "Repaired SQL"
                        repair_event.data = repaired.sql
                        if repaired.usage:
                            state.setdefault("usage", {})["sql_repair"] = repaired.usage
                        # Re-validate and re-execute once.
                        validated, notes = validator.validate(repaired.sql)
                        state["sql"] = validated
                        state.setdefault("notes", []).extend(notes)
                        rows = (
                            self._execute_with_cache(validated, state.get("allowed_objects"))
                            if execute and not plan_only
                            else []
                        )
                        state["rows"] = rows
                        exec_event = TraceEvent(
                            step=f"{plan_step.step}_retry",
                            tool="execute_sql",
                            status="success",
                            detail=f"Executed repaired SQL, rows={len(rows)}",
                            data={"row_count": len(rows)},
                        )
                        trace.append(exec_event)
                        continue
                    except Exception as repair_exc:
                        repair_event.status = "error"
                        repair_event.detail = str(repair_exc)
                        trace.append(TraceEvent(
                            step=f"{plan_step.step}_retry",
                            tool="execute_sql",
                            status="error",
                            detail=f"Repair failed: {repair_exc}",
                        ))
                break

        duration_ms = int((time.perf_counter() - start) * 1000)
        trace_payload = [
            {
                "step": t.step,
                "tool": t.tool,
                "status": t.status,
                "detail": t.detail,
                "data": t.data,
            }
            for t in trace
        ]
        plan_payload = [
            {"step": p.step, "tool": p.tool, "description": p.description} for p in plan_steps
        ]

        return ControllerResult(
            sql=state.get("sql", ""),
            rows=state.get("rows", []),
            figure=state.get("figure"),
            intent=intent,
            trace=trace_payload,
            plan=plan_payload,
            duration_ms=duration_ms,
            notes=state.get("notes"),
            usage=state.get("usage"),
        )
