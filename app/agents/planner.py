from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Dict, List

import openai

from app.config import settings

logger = logging.getLogger(__name__)

try:
    # openai>=1.x
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore


SQL_PLAN_TOOLS = ["retrieve_schema", "draft_sql", "validate_sql", "execute_sql", "summarize"]
VIZ_PLAN_TOOLS = ["retrieve_schema", "plan_viz", "validate_sql", "execute_sql", "render_viz", "summarize"]


@dataclass
class PlanStep:
    step: str
    tool: str
    description: str


class PlannerAgent:
    """
    LLM-driven planner that emits a structured sequence of tool calls.
    Falls back to a deterministic plan if parsing/validation fails.
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

    def _system_prompt(self, intent: str) -> str:
        tools = VIZ_PLAN_TOOLS if intent == "visualization" else SQL_PLAN_TOOLS
        tool_list = ", ".join(tools)
        return (
            "You are a careful planner for a data assistant. "
            "Produce a short JSON object with a 'steps' array. Each step has exactly: "
            '{"tool": one of [' + tool_list + '], "description": brief text}. '
            "Do not include any other keys or commentary. "
            "Keep the plan tight and minimal to answer the question safely."
        )

    def _fallback_plan(self, intent: str) -> List[PlanStep]:
        if intent == "visualization":
            tools = VIZ_PLAN_TOOLS
        else:
            tools = SQL_PLAN_TOOLS
        steps: List[PlanStep] = []
        for idx, tool in enumerate(tools, start=1):
            desc = {
                "retrieve_schema": "retrieve relevant schema context",
                "draft_sql": "draft SQL to answer the question",
                "validate_sql": "validate SQL against allowed objects/columns and add LIMIT",
                "execute_sql": "execute the validated SQL safely",
                "render_viz": "render a Plotly figure from rows and chart spec",
                "plan_viz": "propose SQL and chart spec for the visualization",
                "summarize": "summarize the result and intent",
            }.get(tool, tool)
            steps.append(PlanStep(step=f"step_{idx}", tool=tool, description=desc))
        return steps

    def _has_required_sequence(self, plan_steps: List[PlanStep], intent: str) -> bool:
        required = (
            ["plan_viz", "validate_sql", "execute_sql", "render_viz"]
            if intent == "visualization"
            else ["draft_sql", "validate_sql", "execute_sql"]
        )
        tools = [step.tool for step in plan_steps]
        cursor = -1
        for req in required:
            try:
                cursor = tools.index(req, cursor + 1)
            except ValueError:
                return False
        return True

    def plan(self, question: str, intent: str) -> List[PlanStep]:
        system_prompt = self._system_prompt(intent)
        logger.info("Planning steps for intent=%s question=%s", intent, question)
        tools = set(VIZ_PLAN_TOOLS if intent == "visualization" else SQL_PLAN_TOOLS)

        usage = None

        if not settings.enable_planner:
            logger.info("Planner disabled; using deterministic plan.")
            return self._fallback_plan(intent)

        if self.use_client:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                temperature=0,
            )
            content = resp.choices[0].message.content.strip()
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
            )
            content = resp.choices[0].message["content"].strip()
            if resp.get("usage"):
                usage = resp["usage"]

        # strip fences if present
        if content.startswith("```"):
            content = content.strip("`")
            content = content.replace("json\n", "").replace("JSON\n", "").strip()

        try:
            obj: Dict[str, List[Dict[str, str]]] = json.loads(content)
            raw_steps = obj.get("steps", [])
            plan_steps: List[PlanStep] = []
            for idx, s in enumerate(raw_steps, start=1):
                tool = s.get("tool", "")
                desc = s.get("description", "")
                if tool not in tools:
                    raise ValueError(f"Unknown tool {tool}")
                plan_steps.append(PlanStep(step=f"step_{idx}", tool=tool, description=desc))
            if not plan_steps:
                raise ValueError("Empty plan")
            if not self._has_required_sequence(plan_steps, intent):
                raise ValueError("Plan missing required tool sequence")
            if usage:
                logger.info("Planner usage (model=%s): %s", self.model, usage)
            return plan_steps
        except Exception as exc:
            logger.warning("Planner output invalid (%s). Falling back to deterministic plan.", exc)
            if usage:
                logger.info("Planner usage (model=%s): %s", self.model, usage)
            return self._fallback_plan(intent)
