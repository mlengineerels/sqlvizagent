from dataclasses import dataclass
from typing import Any, Dict, Optional
import json
import logging

import openai

try:
    # openai>=1.x
    from openai import OpenAI
except ImportError:  # pragma: no cover - best-effort compatibility for 0.x
    OpenAI = None  # type: ignore

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class InterpreterResult:
    text: str
    usage: Optional[Dict[str, Any]] = None


class ResponseInterpreter:
    def __init__(self) -> None:
        self.model = settings.openai_interpreter_model or settings.openai_model
        if OpenAI:
            self.client = OpenAI(api_key=settings.openai_api_key)
            self.use_client = True
        else:
            openai.api_key = settings.openai_api_key
            self.client = None
            self.use_client = False

    def _system_prompt(self) -> str:
        return (
            "You are a result interpreter. You must ONLY use the provided result snapshot.\n"
            "If the snapshot lacks the needed info, say so explicitly. Do not guess.\n"
            "Be concise and professional.\n\n"
            "If the user asks about column meanings, use snapshot.column_info to explain them.\n"
            "If column_info is missing or a column is not described, say so.\n\n"
            "If the user asks why a value is high/low, use snapshot.stats (min/max/avg/top values).\n"
            "Be explicit that stats are sample-based, and avoid guessing beyond the snapshot.\n\n"
            "Response format (exact headings):\n"
            "Summary: <one or two sentences>\n"
            "Highlights:\n"
            "- <bullet 1>\n"
            "- <bullet 2>\n"
            "Next step: <one suggested follow-up>"
        )

    def interpret(self, question: str, result_snapshot: Dict[str, Any]) -> InterpreterResult:
        snapshot_json = json.dumps(result_snapshot, ensure_ascii=True, default=str)
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {
                "role": "user",
                "content": f"User question: {question}\nResult snapshot: {snapshot_json}",
            },
        ]

        usage: Optional[Dict[str, Any]] = None

        if self.use_client:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.2,
                max_tokens=250,
            )
            text = resp.choices[0].message.content.strip()
            if getattr(resp, "usage", None):
                usage = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else dict(resp.usage)
        else:
            resp = openai.ChatCompletion.create(
                model=self.model,
                messages=messages,
                temperature=0.2,
                max_tokens=250,
            )
            text = resp.choices[0].message["content"].strip()
            if resp.get("usage"):
                usage = resp["usage"]

        if usage:
            logger.info("Response interpreter usage (model=%s): %s", self.model, usage)

        return InterpreterResult(text=text, usage=usage)
