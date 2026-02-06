**Overview**
This service is a FastAPI app that turns natural-language questions into SQL (and optional visualizations). It uses a deterministic conversation layer (state + turn classification + routing), LLM agents for SQL/viz generation, pgvector for schema retrieval, and Postgres for execution and persistence.

**End-to-End Flow**
1. Web UI (`web/index.html`) calls `POST /api/query` with the user question.
2. Orchestrator loads conversation state, finds the relevant prior result snapshot, and runs turn classification.
3. Conversation planner selects a deterministic execution plan based on the turn type.
4. Query service routes to new query, refinement, interpretation, or visualization.
5. Agent controller runs the plan using SQL/Viz agents plus validation, execution, and optional repair.
6. Results, traces, and snapshots are persisted in Postgres (messages, conversation state, and trace).
7. API responds with SQL, rows/figure, trace/plan, and metadata for the UI.

**Persistence Model**
- `chats` and `messages` tables store full chat history and result snapshots.
- `conversation_states` stores structured, latest state per conversation.
- `conversation_turn_traces` stores per-turn trace and decision reasons.
- `schema_embeddings` stores pgvector schema embeddings.
- `feedback` stores message feedback.

**Troubleshooting and Recent Issues**
- Follow-up average-rating queries failed with joins on `movie_title`. Root cause was missing identity keys in result snapshots, forcing the refiner to guess join columns. Current mitigation: key-preserving snapshots with hidden PK columns plus a refinement gate that triggers rewrite fallback when keys are missing.
- `schema_embeddings.columns` sometimes stored as JSON strings, causing `'str' object has no attribute 'get'` in schema retrieval. Current mitigation: normalize and parse JSON when loading columns.
- Planner-disabled runs still relied on LLM plans that omitted required steps (missing draft SQL). Current mitigation: deterministic plan fallback when the planner is disabled or the plan fails required sequencing.
- Follow-up rewrite is a safety fallback only. Refinement is allowed only when the snapshot declares entity metadata (entity type + hidden PKs). When that metadata is missing, the system falls back to a deterministic rewrite.

**Result Snapshots**
- Snapshots store `columns.display` (shown in the UI) and `columns.hidden` (identity keys, prefixed with `__pk_`).
- Lineage metadata is persisted per snapshot: `entity_type`, `primary_key`, `base_relations`, and `sql_signature`.
- Follow-up refinement uses the hidden identity columns for joins; UI rows never show the hidden columns.

**Top-Level Files**
- `main.py`: FastAPI app setup, CORS, static UI mount.
- `README.md`: project setup, architecture, and API notes.
- `requirements.txt`: Python dependencies.
- `.env`: environment configuration (OpenAI, DB, feature flags).
- `render_viz.py`: utility to render a Plotly `figure` from an API response JSON.
- `notes.txt`: internal project notes.
- `query_test_scenarios.txt`: sample test prompts and scenarios.
- `release_checklist.txt`: release checklist.
- `resp.json`: sample API response payload.
- `viz.html`: rendered Plotly output.

**Config**
- `config/metadata.json`: schema metadata used to build pgvector embeddings, column descriptions, and relationship hints.

**Logs**
- `logs/app.log`: runtime logs written by `app/logging_config.py`.

**App**
- `app/__init__.py`: package marker.
- `app/config.py`: settings and environment configuration.
- `app/db.py`: DB connection + safe read-only SQL execution.
- `app/logging_config.py`: logging setup, console + file.
- `app/vector_store.py`: pgvector schema embedding storage + retrieval.

**App API**
- `app/api/__init__.py`: package marker.
- `app/api/http.py`: FastAPI routes for `/api/query`, chats, and feedback.

**App Agents**
- `app/agents/__init__.py`: package marker.
- `app/agents/intent_classifier.py`: LLM intent classification (retrieval vs visualization vs other).
- `app/agents/router.py`: routes questions to SQL or Viz agent based on intent.
- `app/agents/turn_classifier.py`: turn-type classification for the conversation layer.
- `app/agents/planner.py`: LLM planner for tool steps (SQL/viz plans).
- `app/agents/sql_agent.py`: SQL generation and repair LLM.
- `app/agents/sql_refiner.py`: follow-up SQL refinement against `prev` CTE.
- `app/agents/sql_validator.py`: sqlglot-based validation (tables, columns, LIMIT).
- `app/agents/viz_agent.py`: visualization planner + Plotly renderer.
- `app/agents/response_interpreter.py`: explains results from snapshots.
- `app/agents/table_agent.py`: suggests relevant tables/views.
- `app/agents/knowledge_base.py`: schema cache and allowed objects/columns.
- `app/agents/context_resolver.py`: selects which prior result to follow up on.
- `app/agents/followup_strategy.py`: choose refine vs rewrite for follow-ups.
- `app/agents/followup_rewriter.py`: rewrites follow-up into standalone question.
- `app/agents/followup_resolver.py`: legacy follow-up classifier (interpret/refine/new).

**App Services**
- `app/services/__init__.py`: package marker.
- `app/services/orchestrator.py`: deterministic conversation layer (state + classify + plan + execute + trace + update state).
- `app/services/query_service.py`: main query execution service; builds snapshots, persists messages, routing helpers.
- `app/services/agent_controller.py`: executes planner steps (retrieve schema, draft SQL, validate, execute, render).
- `app/services/conversation_store.py`: Postgres-backed conversation state + turn trace storage.
- `app/services/conversation_planner.py`: maps turn type to deterministic plan.
- SQL signature extraction is built in `app/services/query_service.py`.
- `app/services/result_memory.py`: read assistant result snapshots from messages.
- `app/services/chat_store.py`: create/list/update chats and messages.
- `app/services/column_metadata.py`: column metadata and relationship hints from `config/metadata.json`.
- `app/services/feedback_store.py`: persist feedback on assistant messages.
- `app/services/audit_logger.py`: structured audit logs for queries and metrics.

**Scripts**
- `scripts/embed_metadata.py`: builds pgvector schema embeddings from `config/metadata.json`.
- `scripts/run_followup_eval.py`: evaluation script for follow-up resolver.

**Web**
- `web/index.html`: main chat UI.
- `web/styles.css`: main UI styles.
- `web/app.js`: JS entrypoint for main UI.
- `web/auth.html`: authentication UI (static).
- `web/auth.css`: auth page styles.

**Web JS**
- `web/js/main.js`: UI bootstrapping and event wiring.
- `web/js/actions.js`: send/run query, follow-up handling, table override actions.
- `web/js/api.js`: API client for query, chats, feedback.
- `web/js/ui.js`: DOM state management, render rows/figure/trace, UI helpers.
- `web/js/results.js`: applies API response to UI and builds assistant metadata.
- `web/js/chat.js`: chat history rendering, replay, feedback UI, chat menu.
- `web/js/auth.js`: auth UI mode toggle and submit handler.
