from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol
import logging
import time

from app.config import settings
from app.agents.context_resolver import ContextCandidate, ContextDecision
from app.services.chat_store import append_message
from app.services.result_memory import (
    get_result_snapshot,
    list_recent_result_snapshots,
)

logger = logging.getLogger(__name__)


class OrchestratedService(Protocol):
    context_resolver: Any
    followup_strategy: Any
    followup_rewriter: Any

    def _is_snapshot_fresh(self, snapshot: Dict[str, Any]) -> bool: ...
    def _append_followup_failure(
        self,
        chat_id: Optional[str],
        question: str,
        assistant_text: str,
        action: str,
        reason: Optional[str] = None,
        source_message_id: Optional[str] = None,
        tables_override: Optional[List[str]] = None,
        execute: bool = True,
        plan_only: bool = False,
    ) -> None: ...
    def _log_metrics(
        self,
        action: str,
        question: str,
        duration_ms: int,
        chat_id: Optional[str],
        session_id: Optional[str],
        usage: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None: ...
    def _run_new_query(
        self,
        question: str,
        effective_question: Optional[str],
        execute: bool,
        plan_only: bool,
        tables_override: Optional[List[str]],
        chat_id: Optional[str],
        session_id: Optional[str],
        action_override: str,
        start_time: Optional[float] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
        audit_extra: Optional[Dict[str, Any]] = None,
        usage_prefix: Optional[Dict[str, Any]] = None,
    ): ...
    def interpret_result(
        self,
        question: str,
        chat_id: str,
        message_id: Optional[str] = None,
        session_id: Optional[str] = None,
        pinned_context: bool = False,
    ) -> Dict[str, Any]: ...
    def _refine_from_snapshot(
        self,
        question: str,
        chat_id: str,
        memory_snapshot: Dict[str, Any],
        execute: bool = True,
        plan_only: bool = False,
        session_id: Optional[str] = None,
        source_message_id: Optional[str] = None,
        allow_joins: bool = False,
        refine_question: Optional[str] = None,
        action_override: str = "refine",
        extra_metadata: Optional[Dict[str, Any]] = None,
        audit_extra: Optional[Dict[str, Any]] = None,
    ): ...
    def _build_response(self, **kwargs): ...


@dataclass
class ConversationState:
    question: str
    execute: bool
    plan_only: bool
    tables_override: Optional[List[str]]
    chat_id: Optional[str]
    session_id: Optional[str]
    force_new: bool
    context_message_id: Optional[str]
    start_time: float
    pinned_context: bool = False
    memory: Any = None
    memories: List[Any] = field(default_factory=list)


class Orchestrator:
    def __init__(self, service: OrchestratedService) -> None:
        self.service = service

    def run(
        self,
        question: str,
        execute: bool = True,
        plan_only: bool = False,
        tables_override: Optional[List[str]] = None,
        chat_id: Optional[str] = None,
        session_id: Optional[str] = None,
        force_new: bool = False,
        context_message_id: Optional[str] = None,
    ):
        state = ConversationState(
            question=question,
            execute=execute,
            plan_only=plan_only,
            tables_override=tables_override,
            chat_id=chat_id,
            session_id=session_id,
            force_new=force_new,
            context_message_id=context_message_id,
            start_time=time.perf_counter(),
        )

        if state.chat_id and not state.force_new:
            response = self._handle_context(state)
            if response is not None:
                return response

        return self.service._run_new_query(
            question=state.question,
            effective_question=None,
            execute=state.execute,
            plan_only=state.plan_only,
            tables_override=state.tables_override,
            chat_id=state.chat_id,
            session_id=state.session_id,
            action_override="new",
            start_time=state.start_time,
            extra_metadata=None,
            audit_extra=None,
            usage_prefix=None,
        )

    def _handle_context(self, state: ConversationState):
        if state.context_message_id:
            logger.info(
                "Pinned context requested (chat_id=%s, message_id=%s)",
                state.chat_id,
                state.context_message_id,
            )
            state.memory = get_result_snapshot(state.chat_id, state.context_message_id)
            if state.memory:
                state.pinned_context = True
            else:
                return self._context_missing(state)

        if state.memory:
            state.memories = [state.memory]
        else:
            limit = max(settings.followup_context_max_results, 1)
            state.memories = list_recent_result_snapshots(state.chat_id, limit=limit)
            if settings.followup_context_ttl_minutes > 0:
                state.memories = [
                    m for m in state.memories if self.service._is_snapshot_fresh(m.snapshot)
                ]

        if not state.memories:
            logger.info("No follow-up context found; handling as new query.")
            return None

        candidates = [
            ContextCandidate(
                message_id=m.message_id,
                summary=m.snapshot.get("summary") or "",
                sql=m.snapshot.get("sql") or "",
                columns=self.service._snapshot_display_columns(m.snapshot),
                timestamp=m.snapshot.get("timestamp"),
            )
            for m in state.memories
        ]
        decision: ContextDecision = self.service.context_resolver.resolve(
            state.question,
            candidates=candidates,
            pinned=state.pinned_context,
        )
        logger.info(
            "Context resolver decision: %s (reason=%s)",
            decision.action,
            decision.reason or "",
        )

        resolver_usage: Dict[str, Any] = {}
        if decision.usage:
            resolver_usage["context_resolver"] = decision.usage
        if state.pinned_context and state.memory:
            resolver_usage["pinned_context"] = {"message_id": state.memory.message_id}

        if decision.action == "clarify":
            return self._clarify(state, decision, resolver_usage)

        if decision.action in {"interpret", "refine"}:
            return self._handle_followup(state, decision, resolver_usage)

        logger.info("No follow-up action selected; handling as new query.")
        return None

    def _context_missing(self, state: ConversationState):
        assistant_text = (
            "That loaded result is no longer available. "
            "Please load another result or run this as a new query."
        )
        self.service._append_followup_failure(
            chat_id=state.chat_id,
            question=state.question,
            assistant_text=assistant_text,
            action="context_missing",
            reason="Pinned context not found",
            source_message_id=state.context_message_id,
            tables_override=state.tables_override,
            execute=state.execute,
            plan_only=state.plan_only,
        )
        duration_ms = int((time.perf_counter() - state.start_time) * 1000)
        self.service._log_metrics(
            action="context_missing",
            question=state.question,
            duration_ms=duration_ms,
            chat_id=state.chat_id,
            session_id=state.session_id,
            usage={"pinned_context": {"message_id": state.context_message_id}},
            error="Pinned context not found",
        )
        return self.service._build_response(
            sql="",
            rows=[],
            figure=None,
            intent="retrieval",
            suggested_tables=[],
            trace=[],
            plan=[],
            duration_ms=duration_ms,
            notes=["Pinned context not found"],
            result_snapshot=None,
            action="context_missing",
            assistant_text=assistant_text,
            fallback_question=state.question,
        )

    def _clarify(
        self,
        state: ConversationState,
        decision: ContextDecision,
        resolver_usage: Dict[str, Any],
    ):
        assistant_text = decision.clarification_question or (
            "Which previous result should I use for this follow-up?"
        )
        if state.chat_id:
            append_message(
                chat_id=state.chat_id,
                role="user",
                content=state.question,
                metadata={
                    "kind": "clarify_request",
                    "candidate_message_ids": [m.message_id for m in state.memories],
                },
            )
            append_message(
                chat_id=state.chat_id,
                role="assistant",
                content=assistant_text,
                metadata={
                    "kind": "clarify",
                    "reason": decision.reason,
                    "candidate_message_ids": [m.message_id for m in state.memories],
                },
            )
        duration_ms = int((time.perf_counter() - state.start_time) * 1000)
        self.service._log_metrics(
            action="clarify",
            question=state.question,
            duration_ms=duration_ms,
            chat_id=state.chat_id,
            session_id=state.session_id,
            usage=resolver_usage or None,
        )
        return self.service._build_response(
            sql="",
            rows=[],
            figure=None,
            intent="clarify",
            suggested_tables=[],
            trace=[],
            plan=[],
            duration_ms=duration_ms,
            notes=[],
            result_snapshot=None,
            action="clarify",
            assistant_text=assistant_text,
            usage=resolver_usage or None,
        )

    def _handle_followup(
        self,
        state: ConversationState,
        decision: ContextDecision,
        resolver_usage: Dict[str, Any],
    ):
        memory_by_id = {m.message_id: m for m in state.memories}
        target_memory = state.memory or memory_by_id.get(decision.target_message_id or "")
        if not target_memory:
            assistant_text = (
                "I'm not sure which previous result you mean. "
                "Please tell me which result to use."
            )
            if state.chat_id:
                append_message(
                    chat_id=state.chat_id,
                    role="user",
                    content=state.question,
                    metadata={
                        "kind": "clarify_request",
                        "candidate_message_ids": [m.message_id for m in state.memories],
                    },
                )
                append_message(
                    chat_id=state.chat_id,
                    role="assistant",
                    content=assistant_text,
                    metadata={
                        "kind": "clarify",
                        "reason": "target_missing",
                        "candidate_message_ids": [m.message_id for m in state.memories],
                    },
                )
            duration_ms = int((time.perf_counter() - state.start_time) * 1000)
            self.service._log_metrics(
                action="clarify",
                question=state.question,
                duration_ms=duration_ms,
                chat_id=state.chat_id,
                session_id=state.session_id,
                usage=resolver_usage or None,
                error="Target snapshot not found",
            )
            return self.service._build_response(
                sql="",
                rows=[],
                figure=None,
                intent="clarify",
                suggested_tables=[],
                trace=[],
                plan=[],
                duration_ms=duration_ms,
                notes=["Target snapshot not found"],
                result_snapshot=None,
                action="clarify",
                assistant_text=assistant_text,
                usage=resolver_usage or None,
            )

        last_summary = target_memory.snapshot.get("summary") or ""
        last_sql = target_memory.snapshot.get("sql") or ""

        if decision.action == "interpret":
            return self._handle_interpret(state, target_memory, resolver_usage)

        return self._handle_refine(
            state,
            target_memory,
            last_summary,
            last_sql,
            resolver_usage,
        )

    def _handle_interpret(
        self,
        state: ConversationState,
        target_memory: Any,
        resolver_usage: Dict[str, Any],
    ):
        logger.info("Routing to interpretation for follow-up.")
        try:
            interpretation = self.service.interpret_result(
                state.question,
                state.chat_id,
                message_id=target_memory.message_id,
                session_id=state.session_id,
                pinned_context=state.pinned_context,
            )
            duration_ms = int((time.perf_counter() - state.start_time) * 1000)
            usage = dict(resolver_usage)
            if interpretation.get("usage"):
                usage["interpreter"] = interpretation["usage"]
            self.service._log_metrics(
                action="interpret",
                question=state.question,
                duration_ms=duration_ms,
                chat_id=state.chat_id,
                session_id=state.session_id,
                usage=usage or None,
            )
            return self.service._build_response(
                sql="",
                rows=[],
                figure=None,
                intent="interpretation",
                suggested_tables=[],
                trace=[],
                plan=[],
                duration_ms=duration_ms,
                notes=[],
                result_snapshot=target_memory.snapshot,
                action="interpret",
                assistant_text=interpretation.get("text"),
                usage=usage or None,
            )
        except Exception as exc:
            reason = str(exc)
            assistant_text = (
                "I couldn't interpret the previous result. "
                "You can run this as a new query instead."
            )
            self.service._append_followup_failure(
                chat_id=state.chat_id,
                question=state.question,
                assistant_text=assistant_text,
                action="interpret_failed",
                reason=reason,
                source_message_id=target_memory.message_id,
                tables_override=state.tables_override,
                execute=state.execute,
                plan_only=state.plan_only,
            )
            duration_ms = int((time.perf_counter() - state.start_time) * 1000)
            usage = dict(resolver_usage)
            self.service._log_metrics(
                action="interpret_failed",
                question=state.question,
                duration_ms=duration_ms,
                chat_id=state.chat_id,
                session_id=state.session_id,
                usage=usage or None,
                error=reason,
            )
            return self.service._build_response(
                sql="",
                rows=[],
                figure=None,
                intent="interpretation",
                suggested_tables=[],
                trace=[],
                plan=[],
                duration_ms=duration_ms,
                notes=[reason] if reason else [],
                result_snapshot=target_memory.snapshot,
                action="interpret_failed",
                assistant_text=assistant_text,
                fallback_question=state.question,
                usage=usage or None,
            )

    def _handle_refine(
        self,
        state: ConversationState,
        target_memory: Any,
        last_summary: str,
        last_sql: str,
        resolver_usage: Dict[str, Any],
    ):
        logger.info("Routing to follow-up refinement.")
        try:
            available_cols = ", ".join(
                self.service._snapshot_display_columns(target_memory.snapshot)
            )
            strategy = self.service.followup_strategy.resolve(
                state.question,
                last_summary,
                last_sql,
                available_cols or "(unknown)",
            )
            usage_prefix: Dict[str, Any] = dict(resolver_usage)
            if strategy.usage:
                usage_prefix["followup_strategy"] = strategy.usage
            can_refine, reason = self.service._can_refine_snapshot(
                target_memory.snapshot,
                strategy.strategy,
            )
            if not can_refine:
                fallback_question = self.service._build_rewrite_fallback_question(
                    state.question,
                    target_memory.snapshot,
                )
                return self.service._run_new_query(
                    question=state.question,
                    effective_question=fallback_question,
                    execute=state.execute,
                    plan_only=state.plan_only,
                    tables_override=state.tables_override,
                    chat_id=state.chat_id,
                    session_id=state.session_id,
                    action_override="rewrite_fallback",
                    start_time=state.start_time,
                    extra_metadata={
                        "rewrite_from_followup": True,
                        "rewrite_fallback": True,
                        "rewrite_reason": reason,
                        "source_message_id": target_memory.message_id,
                    },
                    audit_extra={
                        "rewrite_from_followup": True,
                        "rewrite_fallback": True,
                        "rewrite_reason": reason,
                        "source_message_id": target_memory.message_id,
                    },
                    usage_prefix=usage_prefix or None,
                )

            result = self.service._refine_from_snapshot(
                state.question,
                chat_id=state.chat_id,
                memory_snapshot=target_memory.snapshot,
                execute=state.execute,
                plan_only=state.plan_only,
                session_id=state.session_id,
                source_message_id=target_memory.message_id,
                allow_joins=True,
                extra_metadata={"refine_strategy": "transform"},
                audit_extra={"refine_strategy": "transform"},
            )
            result.action = "refine"
            duration_ms = int((time.perf_counter() - state.start_time) * 1000)
            result.duration_ms = duration_ms
            usage: Dict[str, Any] = dict(resolver_usage)
            if strategy.usage:
                usage["followup_strategy"] = strategy.usage
            if result.usage:
                usage.update(result.usage)
            self.service._log_metrics(
                action="refine",
                question=state.question,
                duration_ms=duration_ms,
                chat_id=state.chat_id,
                session_id=state.session_id,
                usage=usage or None,
            )
            result.usage = usage or None
            return result
        except Exception as exc:
            reason = str(exc)
            logger.warning("Refinement failed: %s", exc)
            try:
                fallback_question = self.service._build_rewrite_fallback_question(
                    state.question,
                    target_memory.snapshot,
                )
                usage_prefix: Dict[str, Any] = dict(resolver_usage)
                return self.service._run_new_query(
                    question=state.question,
                    effective_question=fallback_question,
                    execute=state.execute,
                    plan_only=state.plan_only,
                    tables_override=state.tables_override,
                    chat_id=state.chat_id,
                    session_id=state.session_id,
                    action_override="rewrite_fallback",
                    start_time=state.start_time,
                    extra_metadata={
                        "rewrite_from_followup": True,
                        "rewrite_fallback": True,
                        "rewrite_reason": reason,
                        "source_message_id": target_memory.message_id,
                    },
                    audit_extra={
                        "rewrite_from_followup": True,
                        "rewrite_fallback": True,
                        "rewrite_reason": reason,
                        "source_message_id": target_memory.message_id,
                    },
                    usage_prefix=usage_prefix or None,
                )
            except Exception as fallback_exc:
                logger.warning("Rewrite fallback failed: %s", fallback_exc)

            assistant_text = (
                "I couldn't safely refine the previous result. "
                "You can run this as a new query instead."
            )
            self.service._append_followup_failure(
                chat_id=state.chat_id,
                question=state.question,
                assistant_text=assistant_text,
                action="refine_failed",
                reason=reason,
                source_message_id=target_memory.message_id,
                tables_override=state.tables_override,
                execute=state.execute,
                plan_only=state.plan_only,
            )
            duration_ms = int((time.perf_counter() - state.start_time) * 1000)
            usage = dict(resolver_usage)
            self.service._log_metrics(
                action="refine_failed",
                question=state.question,
                duration_ms=duration_ms,
                chat_id=state.chat_id,
                session_id=state.session_id,
                usage=usage or None,
                error=reason,
            )
            return self.service._build_response(
                sql="",
                rows=[],
                figure=None,
                intent="retrieval",
                suggested_tables=[],
                trace=[],
                plan=[],
                duration_ms=duration_ms,
                notes=[reason] if reason else [],
                result_snapshot=target_memory.snapshot,
                action="refine_failed",
                assistant_text=assistant_text,
                fallback_question=state.question,
                usage=usage or None,
            )
