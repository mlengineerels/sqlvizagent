from dataclasses import dataclass
from typing import Optional, Dict, Any
import logging

import openai

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class FollowupStrategyDecision:
    strategy: str  # rewrite | transform
    reason: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None


class FollowupStrategyResolver:
    """
    Chooses how to handle a follow-up against an existing result.
    """

    def __init__(self) -> None:
        self.model = settings.openai_followup_model or settings.openai_model
        if OpenAI:
            self.client = OpenAI(api_key=settings.openai_api_key)
            self.use_client = True
        else:
            openai.api_key = settings.openai_api_key
            self.client = None
            self.use_client = False

    def _system_prompt(self, available_columns: str) -> str:
        return (
            "You decide how to handle a follow-up question with an existing result.\n"
            "Return exactly one label: rewrite or transform.\n"
            "- rewrite: create a standalone question that fully expresses the user's intent.\n"
            "- transform: modify the previous SQL (filter/sort/aggregate) or join tables via prev.\n"
            "Choose transform when the user is clearly operating on the existing result set.\n"
            "Choose rewrite when the question changes scope or needs a new query plan.\n"
            "If the user asks for columns not present in the available columns list, choose rewrite.\n"
            f"Available columns from the last result: {available_columns}\n"
            "Return only the single word label."
        )

    def resolve(
        self,
        question: str,
        last_summary: str,
        last_sql: str,
        available_columns: str,
    ) -> FollowupStrategyDecision:
        messages = [
            {"role": "system", "content": self._system_prompt(available_columns)},
            {
                "role": "user",
                "content": (
                    f"User question: {question}\n"
                    f"Last result summary: {last_summary}\n"
                    f"Last SQL: {last_sql}"
                ),
            },
        ]

        usage: Optional[Dict[str, Any]] = None

        if self.use_client:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0,
                max_tokens=3,
            )
            label = resp.choices[0].message.content.strip().lower()
            if getattr(resp, "usage", None):
                usage = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else dict(resp.usage)
        else:
            resp = openai.ChatCompletion.create(
                model=self.model,
                messages=messages,
                temperature=0,
                max_tokens=3,
            )
            label = resp.choices[0].message["content"].strip().lower()
            if resp.get("usage"):
                usage = resp["usage"]

        if usage:
            logger.info("Follow-up strategy usage (model=%s): %s", self.model, usage)

        strategy = "rewrite" if "rewrite" in label else "transform"
        return FollowupStrategyDecision(strategy=strategy, reason=f"LLM label: {label}", usage=usage)
