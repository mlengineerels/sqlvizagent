from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import json
import logging

import openai

from app.config import settings

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore


@dataclass
class ContextCandidate:
    message_id: str
    summary: str
    sql: str
    columns: List[str]
    timestamp: Optional[str] = None


@dataclass
class ContextDecision:
    action: str  # interpret | refine | new | clarify
    target_message_id: Optional[str] = None
    reason: Optional[str] = None
    confidence: Optional[float] = None
    clarification_question: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None


class ContextResolver:
    """
    LLM-based resolver to decide if a question refers to prior results and which one.
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
            "You resolve whether the user's question refers to a prior result snapshot.\n"
            "You will receive a list of candidate snapshots with ids, summaries, SQL, columns, and timestamps.\n"
            "Return ONLY a JSON object with keys:\n"
            '- "action": one of ["interpret","refine","new","clarify"]\n'
            '- "target_message_id": the chosen snapshot id or null\n'
            '- "reason": short explanation\n'
            '- "confidence": number between 0 and 1\n'
            '- "clarification_question": a brief question or null\n'
            "\n"
            "Guidelines:\n"
            "- interpret: user asks to explain/understand the previous result.\n"
            "- refine: user wants to filter/sort/aggregate/transform a previous result.\n"
            "- new: user asks a new question not grounded in prior results.\n"
            "- clarify: the question seems to refer to prior results but it's ambiguous which one.\n"
            "- Choose clarify if multiple candidates seem plausible.\n"
            "- If you choose new, target_message_id must be null.\n"
            "- If you choose interpret/refine, target_message_id must be set.\n"
            "- Keep clarification_question short and specific.\n"
        )

    def resolve(
        self,
        question: str,
        candidates: List[ContextCandidate],
        pinned: bool = False,
    ) -> ContextDecision:
        if not candidates:
            return ContextDecision(action="new", reason="No candidates provided.", confidence=0.0)

        candidate_payload = [
            {
                "id": c.message_id,
                "summary": c.summary,
                "sql": c.sql,
                "columns": c.columns,
                "timestamp": c.timestamp,
            }
            for c in candidates
        ]

        user_content = {
            "question": question,
            "pinned_context": pinned,
            "candidates": candidate_payload,
        }

        usage: Optional[Dict[str, Any]] = None

        if self.use_client:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": json.dumps(user_content, ensure_ascii=True)},
                ],
                temperature=0,
                max_tokens=220,
            )
            content = resp.choices[0].message.content.strip()
            if getattr(resp, "usage", None):
                usage = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else dict(resp.usage)
        else:
            resp = openai.ChatCompletion.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": json.dumps(user_content, ensure_ascii=True)},
                ],
                temperature=0,
                max_tokens=220,
            )
            content = resp.choices[0].message["content"].strip()
            if resp.get("usage"):
                usage = resp["usage"]

        if content.startswith("```"):
            content = content.strip("`")
            content = content.replace("json\n", "").replace("JSON\n", "").strip()

        try:
            obj = json.loads(content)
        except Exception as exc:
            logger.warning("Context resolver parse failed: %s", exc)
            return ContextDecision(
                action="new",
                reason=f"Parse failed: {exc}",
                confidence=0.0,
                usage=usage,
            )

        action = str(obj.get("action", "new")).strip().lower()
        if action not in {"interpret", "refine", "new", "clarify"}:
            action = "new"
        target_message_id = obj.get("target_message_id")
        if target_message_id is not None:
            target_message_id = str(target_message_id)
        reason = obj.get("reason")
        confidence = obj.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        clarification_question = obj.get("clarification_question")

        return ContextDecision(
            action=action,
            target_message_id=target_message_id,
            reason=reason,
            confidence=confidence,
            clarification_question=clarification_question,
            usage=usage,
        )
