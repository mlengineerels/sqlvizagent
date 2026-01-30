from dataclasses import dataclass
from typing import Optional, Dict, Any, List
import json
import logging

import openai

try:
    from openai import OpenAI
except ImportError: 
    OpenAI = None  

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RefinementResult:
    sql: str
    usage: Optional[Dict[str, Any]] = None


class SQLRefiner:
    """
    Generates a refined SQL query by wrapping the previous SQL in a CTE
    and applying the user's follow-up against the CTE.
    """

    def __init__(self) -> None:
        self.model = settings.openai_model
        if OpenAI:
            self.client = OpenAI(api_key=settings.openai_api_key)
            self.use_client = True
        else:
            openai.api_key = settings.openai_api_key
            self.client = None
            self.use_client = False

    def _system_prompt(self, available_columns: List[str], strict: bool, allow_joins: bool) -> str:
        columns = ", ".join(available_columns) if available_columns else "(unknown)"
        prompt = (
            "You are a SQL refiner for Postgres.\n"
            "Given a user follow-up and the previous SQL, return a refined SQL query.\n"
            "Rules:\n"
            "- Always define: WITH prev AS (<previous_sql>)\n"
            "- The final SELECT must include prev.\n"
            "- You MAY join base tables ONLY if required to add new columns not present in prev.\n"
            "- Any base table must be joined THROUGH prev (prev must be the left or primary source).\n"
            "- Do NOT rewrite the logic of prev unless explicitly requested.\n"
            "- Use only these columns from prev when possible: " + columns + "\n"
            "- SELECT-only. No DML/DDL.\n"
            "- Always include ORDER BY on a reasonable column from the selected columns unless the user explicitly asks for no sorting.\n"
            "- Use DISTINCT by default unless the user explicitly asks for duplicates.\n"
            "- Return ONLY the SQL query. No explanations."
        )
        if not allow_joins:
            prompt += "\nDo NOT join base tables; query ONLY from prev."
        if strict:
            prompt += "\nIf you reference a base table without joining it to prev, the output will be rejected."
        return prompt

    def refine(
        self,
        question: str,
        previous_sql: str,
        available_columns: List[str],
        strict: bool = False,
        allow_joins: bool = False,
    ) -> RefinementResult:
        if not previous_sql:
            raise ValueError("Missing previous SQL for refinement.")

        messages = [
            {"role": "system", "content": self._system_prompt(available_columns, strict, allow_joins)},
            {
                "role": "user",
                "content": (
                    f"User follow-up: {question}\n"
                    f"Previous SQL: {previous_sql}\n"
                    f"Available columns: {json.dumps(available_columns)}"
                ),
            },
        ]

        usage: Optional[Dict[str, Any]] = None

        if self.use_client:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.1,
                max_tokens=300,
            )
            sql = resp.choices[0].message.content.strip()
            if getattr(resp, "usage", None):
                usage = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else dict(resp.usage)
        else:
            resp = openai.ChatCompletion.create(
                model=self.model,
                messages=messages,
                temperature=0.1,
                max_tokens=300,
            )
            sql = resp.choices[0].message["content"].strip()
            if resp.get("usage"):
                usage = resp["usage"]

        if sql.startswith("```"):
            sql = sql.strip("`")
            sql = sql.replace("sql\n", "").replace("SQL\n", "").strip()

        if usage:
            logger.info("SQL refiner usage (model=%s): %s", self.model, usage)

        return RefinementResult(sql=sql, usage=usage)
