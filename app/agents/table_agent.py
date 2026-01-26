from __future__ import annotations

import logging
from typing import List, Optional

import openai

try:
    from openai import OpenAI  # type: ignore
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore

from app.config import settings
from app.agents.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)


class TableAgent:
    """LLM helper to suggest which tables/views to use for a question."""

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb
        self.model = settings.openai_intent_model or settings.openai_model
        if OpenAI:
            self.client = OpenAI(api_key=settings.openai_api_key)
            self.use_client = True
        else:
            openai.api_key = settings.openai_api_key
            self.client = None
            self.use_client = False

    def suggest(self, question: str, top_k: int = 2) -> List[str]:
        allowed = self.kb.allowed_objects()
        if not allowed:
            return []

        system_prompt = (
            "You are a schema assistant. Given a user question and the list of available tables/views, "
            "return the names of the most relevant tables/views (comma separated) that should be used to answer the question. "
            "Respond with only the names, separated by commas. Available objects:\n"
            + "\n".join(allowed)
        )

        try:
            if self.use_client:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": question},
                    ],
                    temperature=0,
                    max_tokens=64,
                )
                text = resp.choices[0].message.content or ""
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
                    max_tokens=64,
                )
                text = resp.choices[0].message["content"] or ""
                usage = resp.get("usage")
        except Exception as exc:
            logger.warning("Table suggestion failed: %s", exc)
            return []

        names = [n.strip().lower() for n in text.split(",") if n.strip()]
        # filter to allowed and dedupe
        filtered = []
        allowed_set = {a.lower() for a in allowed}
        for name in names:
            if name in allowed_set and name not in filtered:
                filtered.append(name)
            if len(filtered) >= top_k:
                break
        if usage:
            logger.info("Table suggestion usage (model=%s): %s", self.model, usage)
        return filtered
