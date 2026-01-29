from dataclasses import dataclass
from typing import Optional, Dict, Any, List
import json
import logging

import openai

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore

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

    def _system_prompt(self, available_columns: List[str], strict: bool) -> str:
        columns = ", ".join(available_columns) if available_columns else "(unknown)"
        prompt = (
            "You are a SQL refiner for Postgres.\n"
            "Given a user follow-up and the previous SQL, return a refined SQL query.\n"
            "Rules:\n"
            "- Use a CTE named prev: WITH prev AS (<previous_sql>)\n"
            "- Query ONLY from prev. Do not reference base tables directly.\n"
            "- Use only these columns from prev: " + columns + "\n"
            "- Return ONLY the SQL query, no explanations.\n"
            "- SELECT-only, no DML/DDL."
        )
        if strict:
            prompt += "\nIf you reference any base table instead of prev, your output will be rejected."
        return prompt

    def refine(
        self,
        question: str,
        previous_sql: str,
        available_columns: List[str],
        strict: bool = False,
    ) -> RefinementResult:
        if not previous_sql:
            raise ValueError("Missing previous SQL for refinement.")

        messages = [
            {"role": "system", "content": self._system_prompt(available_columns, strict)},
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
