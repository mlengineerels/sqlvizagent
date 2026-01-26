# Project SQL (Vector-first NL2SQL)

FastAPI service that routes natural language questions to SQL or visualizations, using pgvector-backed schema retrieval and OpenAI models.

## Quick start
1) Create/activate a virtualenv.
2) Install deps: `pip install -r requirements.txt`.
3) Set your env in `.env` (OpenAI keys, DB connection, embedding model). You can copy `.env` to your own config file if you prefer and point `env_file` accordingly.
4) Ensure Postgres has pgvector: `CREATE EXTENSION vector;`.
5) Populate `schema_embeddings` via the sync script:
   ```bash
   python scripts/embed_metadata.py
   ```
6) Run the API: `uvicorn main:app --reload`.
7) Open `http://localhost:8000/` for the chat UI.

## How it works
- **Vector-first schema**: The service retrieves schema context from the `schema_embeddings` table (pgvector) rather than shipping metadata in prompts.
- **Planner + controller**: A planner produces a structured step list, and a controller executes tools (schema retrieval, SQL draft, sqlglot validation, execution, optional repair, viz render) with full trace.
- **Intent + routing**: Lightweight model for intent classification; SQL agent uses a larger model for query generation; viz agent for chart specs + Plotly rendering.
- **Safety**: Only SELECT is allowed; allowed objects/columns come from vector entries; default LIMIT applied; repair loop on DB errors.
- **Caching (optional)**: Enable row-level cache with `ENABLE_QUERY_CACHE` in `.env`.

## Refreshing embeddings (daily/CI)
- Maintain your schema description in `config/metadata.json`.
- Run the sync script to rebuild embeddings (clears existing):
  ```bash
  python scripts/embed_metadata.py
  ```
  It upserts into `schema_embeddings` using `OPENAI_EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS` from `.env`.

## API
- `GET /health` – liveness.
- `POST /api/query` – `{ "question": "...", "execute": true|false }`; returns SQL, rows, optional figure, intent.

## UI
- Chat-style interface at `/`: shows SQL, tabular rows (first 20), and inline Plotly chart for viz intents plus a plan-only toggle and a trace pane to inspect planner/controller steps.

## Env/config
- OpenAI: `OPENAI_API_KEY`, `OPENAI_MODEL` (SQL), `OPENAI_INTENT_MODEL` (intent), `OPENAI_EMBEDDING_MODEL`.
- DB: either `DATABASE_URL` or `DB_*` fields.
- Vector: `EMBEDDING_DIMENSIONS` (matches embedding model), pgvector extension required.
- Cache: `ENABLE_QUERY_CACHE=true` to cache rows in-memory.

## Notes
- Runtime does **not** read `metadata.json`; it relies on the vector store populated by the sync script.
- If embeddings are empty or pgvector isn’t available, schema context and safety lists will be empty.

## Architecture (high-level)
```mermaid
flowchart TD
    UI[Web UI / curl] --> API[/FastAPI /api/query/]
    API --> Intent[Intents (small OpenAI model)]
    API --> Router{Router}
    Intent --> Router
    Router -->|retrieval| SQLAgent[SQL Agent (OpenAI SQL model)]
    Router -->|visualization| VizAgent[Viz Agent (OpenAI + Plotly)]
    SQLAgent --> Vector[pgvector: schema_embeddings]
    VizAgent --> Vector
    SQLAgent --> DB[(Postgres)]
    VizAgent --> DB
    Vector --> DB
    DB --> API
    SQLAgent -.repair if DB error.- DB
    DB --> UI
    VizAgent --> UI
```

**Key pieces**
- **pgvector**: stores schema embeddings (`schema_embeddings`); populated by `scripts/embed_metadata.py`.
- **Knowledge base**: loads tables/views from pgvector only.
- **Guards**: enforce SELECT-only, allowed objects/columns, default LIMIT, and a single repair attempt.
- **Models**: intent (small model), SQL generation (larger model), optional repair (can use smaller model).
