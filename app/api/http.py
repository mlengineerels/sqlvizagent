# app/api/http.py
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.chat_store import (
    create_chat,
    delete_chat,
    get_chat,
    list_chats,
    rename_chat,
)
from app.services.query_service import QueryService, QueryResponse
from app.services.feedback_store import add_feedback, list_feedback
from app.services.audit_logger import log_audit_event

router = APIRouter()
service = QueryService()
logger = logging.getLogger(__name__)

class QueryRequest(BaseModel):
    question: str = Field(..., description="Natural language question about movies.")
    execute: bool = Field(
        True, description="If true, execute the query and return results."
    )
    plan_only: bool = Field(
        False, description="If true, plan/generate SQL but skip execution."
    )
    force_new: bool = Field(
        False, description="If true, bypass follow-up handling and treat as new query."
    )
    context_message_id: Optional[str] = Field(
        None, description="Message id to use as follow-up context (from Load result)."
    )
    session_id: Optional[str] = Field(None, description="Client conversation/session id.")
    chat_id: Optional[str] = Field(None, description="Chat thread id for persistence.")
    tables: Optional[List[str]] = Field(None, description="Explicit tables/views to use (overrides suggestions).")

class ResultSnapshot(BaseModel):
    sql: str
    row_count: int
    rows_sample: List[Dict[str, Any]]
    columns: List[str]
    stats: Optional[Dict[str, Any]] = None
    chart: Optional[Dict[str, Any]] = None
    summary: Optional[str] = None
    timestamp: datetime
    truncated: bool = False
    column_info: Optional[Dict[str, List[Dict[str, Any]]]] = None


class QueryResult(BaseModel):
    sql: str
    rows: List[Dict[str, Any]]
    figure: Optional[Dict[str, Any]] = None
    intent: Optional[str] = None
    suggested_tables: Optional[List[str]] = None
    trace: Optional[List[Dict[str, Any]]] = None
    plan: Optional[List[Dict[str, Any]]] = None
    duration_ms: Optional[int] = None
    notes: Optional[List[str]] = None
    result_snapshot: Optional[ResultSnapshot] = None
    action: Optional[str] = None
    assistant_text: Optional[str] = None
    fallback_question: Optional[str] = None


def _to_query_result(result: QueryResponse) -> QueryResult:
    return QueryResult(
        sql=result.sql,
        rows=result.rows,
        figure=result.figure,
        intent=result.intent,
        suggested_tables=result.suggested_tables,
        trace=result.trace,
        plan=result.plan,
        duration_ms=result.duration_ms,
        notes=result.notes,
        result_snapshot=result.result_snapshot,
        action=result.action,
        assistant_text=result.assistant_text,
        fallback_question=result.fallback_question,
    )


class ChatSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ChatMessage(BaseModel):
    id: str
    chat_id: str
    role: str
    content: str
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime


class ChatDetail(BaseModel):
    chat: ChatSummary
    messages: List[ChatMessage]


class FeedbackRequest(BaseModel):
    chat_id: str
    message_id: str
    rating: int = Field(..., description="1 for upvote, -1 for downvote.")
    comment: Optional[str] = Field(None, description="Optional feedback comment.")


class FeedbackResponse(BaseModel):
    id: str
    chat_id: str
    message_id: str
    rating: int
    comment: Optional[str] = None
    created_at: datetime


class ReplayEvent(BaseModel):
    id: str
    role: str
    content: str
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime
    feedback: Optional[FeedbackResponse] = None


class ReplayResponse(BaseModel):
    chat: ChatSummary
    events: List[ReplayEvent]


class CreateChatRequest(BaseModel):
    title: Optional[str] = Field(None, description="Optional chat title.")


class RenameChatRequest(BaseModel):
    title: str = Field(..., min_length=1, description="New chat title.")


class DeleteChatResponse(BaseModel):
    ok: bool = True

@router.post("/query", response_model=QueryResult)
async def query_endpoint(payload: QueryRequest) -> QueryResult:
    try:
        result = service.handle_question(
            question=payload.question,
            execute=payload.execute,
            plan_only=payload.plan_only,
            tables_override=payload.tables,
            chat_id=payload.chat_id,
            session_id=payload.session_id,
            force_new=payload.force_new,
            context_message_id=payload.context_message_id,
        )
        return _to_query_result(result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Query handling failed")
        raise HTTPException(status_code=500, detail=str(e))




@router.get("/chats", response_model=List[ChatSummary])
async def list_chats_endpoint() -> List[ChatSummary]:
    try:
        return [ChatSummary(**c) for c in list_chats()]
    except Exception as e:
        logger.exception("Failed to list chats")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chats", response_model=ChatSummary)
async def create_chat_endpoint(payload: CreateChatRequest) -> ChatSummary:
    try:
        title = (payload.title or "").strip() or "New chat"
        chat_id = create_chat(title=title)
        chat = get_chat(chat_id)
        if not chat:
            raise ValueError("Failed to fetch newly created chat")
        return ChatSummary(**chat["chat"])
    except Exception as e:
        logger.exception("Failed to create chat")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chats/{chat_id}", response_model=ChatDetail)
async def get_chat_endpoint(chat_id: str) -> ChatDetail:
    try:
        chat = get_chat(chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found")
        return ChatDetail(
            chat=ChatSummary(**chat["chat"]),
            messages=[ChatMessage(**m) for m in chat["messages"]],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to fetch chat")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/chats/{chat_id}", response_model=ChatSummary)
async def rename_chat_endpoint(chat_id: str, payload: RenameChatRequest) -> ChatSummary:
    try:
        chat = get_chat(chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found")
        rename_chat(chat_id, payload.title.strip())
        chat = get_chat(chat_id)
        if not chat:
            raise ValueError("Failed to fetch renamed chat")
        return ChatSummary(**chat["chat"])
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to rename chat")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/chats/{chat_id}", response_model=DeleteChatResponse)
async def delete_chat_endpoint(chat_id: str) -> DeleteChatResponse:
    try:
        chat = get_chat(chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found")
        delete_chat(chat_id)
        return DeleteChatResponse(ok=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to delete chat")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/feedback", response_model=FeedbackResponse)
async def feedback_endpoint(payload: FeedbackRequest) -> FeedbackResponse:
    if payload.rating not in (-1, 1):
        raise HTTPException(status_code=400, detail="Rating must be -1 or 1.")
    try:
        feedback = add_feedback(
            chat_id=payload.chat_id,
            message_id=payload.message_id,
            rating=payload.rating,
            comment=payload.comment,
        )
        log_audit_event(
            event_type="feedback",
            action="feedback",
            question="",
            chat_id=payload.chat_id,
            session_id=None,
            sql=None,
            tables=[],
            row_count=None,
            extra={"message_id": payload.message_id, "rating": payload.rating},
        )
        return FeedbackResponse(**feedback)
    except Exception as e:
        logger.exception("Failed to store feedback")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chats/{chat_id}/replay", response_model=ReplayResponse)
async def replay_endpoint(chat_id: str) -> ReplayResponse:
    try:
        chat = get_chat(chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found")
        feedback_entries = list_feedback(chat_id)
        feedback_map = {f["message_id"]: f for f in feedback_entries}
        events: List[ReplayEvent] = []
        for message in chat["messages"]:
            feedback = feedback_map.get(message["id"])
            events.append(
                ReplayEvent(
                    id=message["id"],
                    role=message["role"],
                    content=message["content"],
                    metadata=message.get("metadata"),
                    created_at=message["created_at"],
                    feedback=FeedbackResponse(**feedback) if feedback else None,
                )
            )
        return ReplayResponse(
            chat=ChatSummary(**chat["chat"]),
            events=events,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to build replay")
        raise HTTPException(status_code=500, detail=str(e))
