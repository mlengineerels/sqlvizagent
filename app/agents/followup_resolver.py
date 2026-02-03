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
class FollowupDecision:
    action: str  # interpret | refine | new
    reason: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None


class FollowupResolver:
    """
    Classifies a user follow-up relative to the last result.
    """

    def __init__(self) -> None:
        self.model = (
            settings.openai_followup_model
            or settings.openai_intent_model
            or settings.openai_model
        )
        if OpenAI:
            self.client = OpenAI(api_key=settings.openai_api_key)
            self.use_client = True
        else:
            openai.api_key = settings.openai_api_key
            self.client = None
            self.use_client = False

    def _system_prompt(self) -> str:
        return (
            "You classify follow-up questions given the last result context.\n"
            "Return exactly one label: interpret, refine, or new.\n"
            "- interpret: user wants explanation/meaning of the last result.\n"
            "- refine: user wants to filter/sort/transform the last result.\n"
            "- new: user is asking a new question unrelated to the last result.\n"
            "\n"
            "Important: Only choose refine if the user explicitly refers to the prior result\n"
            "(e.g., \"from the results\", \"those\", \"these\", \"that list\", \"same as before\").\n"
            "If the question can stand alone without the previous result, choose new.\n"
            "If unsure, choose new.\n"
            "Return only the single word label."
        )

    def resolve(self, question: str, last_summary: str, last_sql: str) -> FollowupDecision:
        messages = [
            {"role": "system", "content": self._system_prompt()},
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
            logger.info("Follow-up resolver usage (model=%s): %s", self.model, usage)

        if "interpret" in label:
            action = "interpret"
        elif "refine" in label:
            action = "refine"
        else:
            action = "new"

        reason = f"LLM label: {label}"

        return FollowupDecision(action=action, reason=reason, usage=usage)
