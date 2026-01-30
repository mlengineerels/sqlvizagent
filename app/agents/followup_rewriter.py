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
class FollowupRewriteResult:
    question: str
    usage: Optional[Dict[str, Any]] = None


class FollowupRewriter:
    """
    Rewrites a follow-up into a standalone question using the prior result context.
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

    def _system_prompt(self) -> str:
        return (
            "You rewrite follow-up questions into standalone questions.\n"
            "Use the last result context to resolve references like 'those' or 'from above'.\n"
            "Do not add new intent beyond the user's request.\n"
            "Return only the rewritten question, no explanations."
        )

    def rewrite(self, question: str, last_summary: str, last_sql: str) -> FollowupRewriteResult:
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {
                "role": "user",
                "content": (
                    f"Follow-up question: {question}\n"
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
                temperature=0.1,
                max_tokens=200,
            )
            rewritten = resp.choices[0].message.content.strip()
            if getattr(resp, "usage", None):
                usage = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else dict(resp.usage)
        else:
            resp = openai.ChatCompletion.create(
                model=self.model,
                messages=messages,
                temperature=0.1,
                max_tokens=200,
            )
            rewritten = resp.choices[0].message["content"].strip()
            if resp.get("usage"):
                usage = resp["usage"]

        if rewritten.startswith("```"):
            rewritten = rewritten.strip("`")
            rewritten = rewritten.replace("text\n", "").replace("TEXT\n", "").strip()

        if usage:
            logger.info("Follow-up rewrite usage (model=%s): %s", self.model, usage)

        return FollowupRewriteResult(question=rewritten, usage=usage)
