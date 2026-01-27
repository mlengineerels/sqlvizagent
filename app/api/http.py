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
from app.services.query_service import QueryService

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
    session_id: Optional[str] = Field(None, description="Client conversation/session id.")
    chat_id: Optional[str] = Field(None, description="Chat thread id for persistence.")
    tables: Optional[List[str]] = Field(None, description="Explicit tables/views to use (overrides suggestions).")

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
        )
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
        )
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
