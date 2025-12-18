# app/agents/sql_agent.py
from dataclasses import dataclass
from typing import Optional, Dict, Any
import logging

import openai

try:
    # Available in openai>=1.x
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore

from app.config import settings
from app.agents.knowledge_base import KnowledgeBase
from app.vector_store import VectorStore

logger = logging.getLogger(__name__)

@dataclass
class SQLResult:
    sql: str
    debug_prompt: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None

class SQLAgent:
    def __init__(self, kb: KnowledgeBase, vector_store: Optional[VectorStore] = None):
        self.kb = kb
        self.vector_store = vector_store
        self.model = settings.openai_model
        if OpenAI:
            self.client = OpenAI(api_key=settings.openai_api_key)
            self.use_client = True
        else:
            openai.api_key = settings.openai_api_key
            self.client = None
            self.use_client = False

    def _system_prompt(self, relevant_schema: str = "") -> str:
        dialect = self.kb.dialect
        allowed_objects = ", ".join(self.kb.allowed_objects()) or "the provided tables/views"

        return f"""
You are an expert {dialect} SQL query generator for the MovieLens dataset.

You MUST follow these rules:

1. Only query from the allowed tables/views: {allowed_objects}
2. Only generate read-only SELECT queries.
3. Use {dialect} syntax.
4. Use explicit WHERE, GROUP BY, ORDER BY, LIMIT clauses as needed.
5. Do NOT modify data (no INSERT, UPDATE, DELETE, CREATE, DROP, etc.).
6. Respond with ONLY the SQL query. No explanations, comments, or markdown.
7. Use clear, aliased column names in SELECT when aggregating.

Schema context (from pgvector retrieval only):
{relevant_schema or "None retrieved; use best judgment with allowed objects only."}
""".strip()

    def generate_sql(self, question: str) -> SQLResult:
        relevant = ""
        if self.vector_store:
            relevant = self.vector_store.get_relevant_schema(question, top_k=5)
        system_prompt = self._system_prompt(relevant_schema=relevant)

        logger.info("Generating SQL for question: %s", question)

        usage: Optional[Dict[str, Any]] = None

        if self.use_client:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                temperature=0.1,
            )
            raw_sql = resp.choices[0].message.content.strip()
            if getattr(resp, "usage", None):
                usage = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else dict(resp.usage)
        else:
            resp = openai.ChatCompletion.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                temperature=0.1,
            )
            raw_sql = resp.choices[0].message["content"].strip()
            if resp.get("usage"):
                usage = resp["usage"]

        # sanitize markdown fences if any
        if raw_sql.startswith("```"):
            raw_sql = raw_sql.strip("`")
            raw_sql = raw_sql.replace("sql\n", "").replace("SQL\n", "").strip()

        logger.info("Generated SQL (normal): %s", raw_sql)

        return SQLResult(sql=raw_sql, debug_prompt=system_prompt, usage=usage)

    def repair_sql(self, question: str, bad_sql: str, db_error: str) -> SQLResult:
        """Ask the model to repair SQL given a DB error."""
        schema_text = self.kb.as_schema_text()
        dialect = self.kb.dialect
        allowed_objects = ", ".join(self.kb.allowed_objects()) or "the provided tables/views"

        system_prompt = f"""
You are an expert {dialect} SQL fixer. Given a user question, a faulty SQL, and the database error, return a corrected SELECT query.
Rules:
- Only query from the allowed tables/views: {allowed_objects}
- Only generate read-only SELECT queries.
- Use {dialect} syntax.
- Add a LIMIT if missing to keep results small (<= 200).
- Respond with ONLY the SQL query. No explanations, comments, or markdown.

Schema:
{schema_text}
""".strip()

        logger.info("Repairing SQL after DB error: %s", db_error)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Question: {question}\nBad SQL: {bad_sql}\nError: {db_error}"},
        ]

        usage: Optional[Dict[str, Any]] = None

        if self.use_client:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.1,
            )
            raw_sql = resp.choices[0].message.content.strip()
            if getattr(resp, "usage", None):
                usage = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else dict(resp.usage)
        else:
            resp = openai.ChatCompletion.create(
                model=self.model,
                messages=messages,
                temperature=0.1,
            )
            raw_sql = resp.choices[0].message["content"].strip()
            if resp.get("usage"):
                usage = resp["usage"]

        if raw_sql.startswith("```"):
            raw_sql = raw_sql.strip("`")
            raw_sql = raw_sql.replace("sql\n", "").replace("SQL\n", "").strip()

        logger.info("Repaired SQL: %s", raw_sql)
        return SQLResult(sql=raw_sql, debug_prompt=system_prompt, usage=usage)
