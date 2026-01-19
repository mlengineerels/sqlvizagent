# app/api/http.py
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

from app.services.query_service import QueryService

router = APIRouter()
service = QueryService()
logger = logging.getLogger(__name__)

class QueryRequest(BaseModel):
    question: str = Field(..., description="Natural language question about movies.")
    execute: bool = Field(
        True, description="If true, execute the query and return results."
    )
    session_id: Optional[str] = Field(None, description="Client conversation/session id.")
    tables: Optional[List[str]] = Field(None, description="Explicit tables/views to use (overrides suggestions).")

class QueryResult(BaseModel):
    sql: str
    rows: List[Dict[str, Any]]
    figure: Optional[Dict[str, Any]] = None
    intent: Optional[str] = None
    suggested_tables: Optional[List[str]] = None

@router.post("/query", response_model=QueryResult)
async def query_endpoint(payload: QueryRequest) -> QueryResult:
    try:
        result = service.handle_question(
            question=payload.question,
            execute=payload.execute,
            tables_override=payload.tables,
        )
        return QueryResult(
            sql=result.sql,
            rows=result.rows,
            figure=result.figure,
            intent=result.intent,
            suggested_tables=result.suggested_tables,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Query handling failed")
        raise HTTPException(status_code=500, detail=str(e))
