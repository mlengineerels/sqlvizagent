# app/services/query_service.py
import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from app.agents.knowledge_base import KnowledgeBase
from app.agents.router import RouterAgent
from app.agents.sql_agent import SQLAgent, SQLResult
from app.agents.viz_agent import VizAgent, VisualizationResult
from app.db import execute_readonly_query
from app.config import settings

logger = logging.getLogger(__name__)

@dataclass
class QueryResponse:
    sql: str
    rows: List[Dict[str, Any]]
    figure: Optional[Dict[str, Any]] = None
    intent: Optional[str] = None

class QueryService:
    def __init__(self, kb: Optional[KnowledgeBase] = None):
        self.kb = kb or KnowledgeBase()
        self.router = RouterAgent()
        self.sql_agent = SQLAgent(self.kb)
        self.viz_agent = VizAgent(self.kb)
        self.cache: Optional[Dict[str, List[Dict[str, Any]]]] = {} if settings.enable_query_cache else None

    def handle_question(self, question: str, execute: bool = True) -> QueryResponse:
        decision = self.router.route(question)

        if decision.agent == "viz_agent":
            viz_result: VisualizationResult = self.viz_agent.generate_viz(question, execute=execute, cache=self.cache)
            if decision.usage:
                logger.info("Intent classifier usage: %s", decision.usage)
            if viz_result.usage:
                logger.info("Viz generator usage: %s", viz_result.usage)
            return QueryResponse(sql=viz_result.sql, rows=viz_result.rows, figure=viz_result.figure, intent=decision.intent)

        if decision.agent == "sql_agent":
            sql_result: SQLResult = self.sql_agent.generate_sql(question)

            # Log token usage for cost visibility.
            if decision.usage:
                logger.info("Intent classifier usage: %s", decision.usage)
            if sql_result.usage:
                logger.info("SQL generator usage: %s", sql_result.usage)

            rows: List[Dict[str, Any]] = []
            if execute:
                try:
                    if self.cache is not None and sql_result.sql in self.cache:
                        rows = self.cache[sql_result.sql]
                    else:
                        rows = execute_readonly_query(
                            sql_result.sql,
                            allowed_objects=self.kb.allowed_objects(),
                            allowed_columns=self.kb.allowed_columns(),
                        )
                        if self.cache is not None:
                            self.cache[sql_result.sql] = rows
                except ValueError as exc:
                    # Attempt a single repair if the DB execution failed.
                    repaired = self.sql_agent.repair_sql(question, sql_result.sql, str(exc))
                    rows = execute_readonly_query(
                        repaired.sql,
                        allowed_objects=self.kb.allowed_objects(),
                        allowed_columns=self.kb.allowed_columns(),
                    )
                    if self.cache is not None:
                        self.cache[repaired.sql] = rows
                    return QueryResponse(sql=repaired.sql, rows=rows, intent=decision.intent)

            return QueryResponse(sql=sql_result.sql, rows=rows, intent=decision.intent)

        raise ValueError(f"No suitable agent for intent '{decision.intent}'.")
