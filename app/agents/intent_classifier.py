# app/agents/intent_classifier.py
from dataclasses import dataclass
from typing import Optional, Dict, Any
import logging

import openai

from app.config import settings

logger = logging.getLogger(__name__)

try:
    # openai>=1.x
    from openai import OpenAI
except ImportError:  # pragma: no cover - best-effort compatibility for 0.x
    OpenAI = None  # type: ignore


@dataclass
class IntentPrediction:
    intent: str  # expected: "retrieval" or "other"
    reason: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None


class IntentClassifier:
    """
    Lightweight LLM-based intent detector to decide whether a question
    should be routed to the SQL agent.
    """

    def __init__(self) -> None:
        self.model = settings.openai_intent_model or settings.openai_model
        if OpenAI:
            self.client = OpenAI(api_key=settings.openai_api_key)
            self.use_client = True
        else:
            openai.api_key = settings.openai_api_key
            self.client = None
            self.use_client = False

    def predict(self, question: str) -> IntentPrediction:
        system_prompt = (
            "You classify user questions.\n"
            "- If the question can be answered by running a SQL query over the MovieLens view, respond with exactly: retrieval\n"
            "- If the user is asking for a chart/graph/plot or any visualization, respond with exactly: visualization\n"
            "- Otherwise respond with exactly: other\n"
            "Return only the single word label."
        )

        logger.info("Classifying intent for question: %s", question)

        usage: Optional[Dict[str, Any]] = None

        if self.use_client:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                temperature=0,
                max_tokens=3,
            )
            label = resp.choices[0].message.content.strip().lower()
            if getattr(resp, "usage", None):
                usage = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else dict(resp.usage)
        else:
            resp = openai.ChatCompletion.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                temperature=0,
                max_tokens=3,
            )
            label = resp.choices[0].message["content"].strip().lower()
            if resp.get("usage"):
                usage = resp["usage"]

        if "visual" in label:
            intent = "visualization"
        elif "retrieval" in label:
            intent = "retrieval"
        else:
            intent = "other"
        logger.info("Intent classified as: %s (raw: %s)", intent, label)
        return IntentPrediction(intent=intent, reason=f"LLM label: {label}", usage=usage)
