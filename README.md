# Project SQL

Lightweight scaffold for a FastAPI-based SQL agent service.

## Getting started

1) Create and activate a virtual environment.
2) Install dependencies: `pip install -r requirements.txt`.
3) Run the API: `uvicorn main:app --reload`.

The API exposes:
- `GET /health` for a simple health check.
- `POST /query` with body `{"query": "select * from users"}` to execute SQL against the configured database.

Configuration lives in `app/config.py` (environment driven via `.env`). Metadata for the knowledge base sits in `config/metadata.json`.
