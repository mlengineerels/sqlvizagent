# app/agents/router.py
import re
from dataclasses import dataclass
from typing import Literal, Optional, Dict, Any
import re

from app.agents.intent_classifier import IntentClassifier, IntentPrediction

AgentName = Literal["sql_agent", "viz_agent", "unknown"]

@dataclass
class RouteDecision:
    agent: AgentName
    reason: str
    intent: str = "retrieval"
    usage: Optional[Dict[str, Any]] = None

class RouterAgent:
    """
    Routes questions based on LLM intent classification. Currently routes
    retrieval intents to the SQL agent.
    """

    VIZ_KEYWORDS = [
        r"\bplot\b",
        r"\bchart\b",
        r"\bgraph\b",
        r"\bvisual",
        r"\bbar chart\b",
        r"\bline chart\b",
        r"\bscatter\b",
        r"\bpie\b",
    ]

    def __init__(self) -> None:
        self.classifier = IntentClassifier()

    def route(self, question: str) -> RouteDecision:
        q = question.lower()

        # Fast heuristic for visualization intents to reduce LLM calls and avoid misclassification.
        for pattern in self.VIZ_KEYWORDS:
            if re.search(pattern, q):
                return RouteDecision(
                    agent="viz_agent",
                    reason=f"Matched visualization keyword: {pattern}",
                    intent="visualization",
                    usage=None,
                )

        try:
            prediction: IntentPrediction = self.classifier.predict(question)
        except Exception as exc:
            # If classification fails, refuse routing to avoid unsafe SQL generation.
            return RouteDecision(
                agent="unknown",
                reason=f"Intent classification failed: {exc}",
                intent="unknown",
            )

        if prediction.intent == "retrieval":
            return RouteDecision(
                agent="sql_agent",
                reason=prediction.reason or "LLM intent: retrieval",
                intent=prediction.intent,
                usage=prediction.usage,
            )
        if prediction.intent == "visualization":
            return RouteDecision(
                agent="viz_agent",
                reason=prediction.reason or "LLM intent: visualization",
                intent=prediction.intent,
                usage=prediction.usage,
            )

        return RouteDecision(
            agent="unknown",
            reason=prediction.reason or "LLM intent not retrieval",
            intent=prediction.intent,
            usage=prediction.usage,
        )
