# app/services/query_service.py
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.agents.knowledge_base import KnowledgeBase
from app.agents.router import RouterAgent
from app.agents.sql_agent import SQLAgent
from app.agents.viz_agent import VizAgent
from app.agents.table_agent import TableAgent
from app.config import settings
from app.services.agent_controller import AgentController, ControllerResult
from app.vector_store import VectorStore

logger = logging.getLogger(__name__)


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
        self.viz_agent = VizAgent(self.kb)
        self.table_agent = TableAgent(self.kb)
        self.cache: Optional[Dict[str, List[Dict[str, Any]]]] = {} if settings.enable_query_cache else None
        self.controller = AgentController(
            kb=self.kb,
            vector_store=self.vector_store,
            sql_agent=self.sql_agent,
            viz_agent=self.viz_agent,
            cache=self.cache,
        )

    def handle_question(
        self,
        question: str,
        execute: bool = True,
        plan_only: bool = False,
        tables_override: Optional[List[str]] = None,
    ) -> QueryResponse:
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

        return QueryResponse(
            sql=controller_result.sql,
            rows=controller_result.rows,
            figure=controller_result.figure,
            intent=controller_result.intent,
            suggested_tables=suggested_tables,
            trace=controller_result.trace,
            plan=controller_result.plan,
            duration_ms=controller_result.duration_ms,
            notes=controller_result.notes,
        )
