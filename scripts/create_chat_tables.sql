-- Chat persistence tables for multi-chat UI.
-- Run manually once on your Postgres database.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS chats (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  title text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  chat_id uuid NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('user', 'assistant')),
  content text NOT NULL,
  metadata jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chats_updated_at_idx ON chats (updated_at DESC);
CREATE INDEX IF NOT EXISTS messages_chat_id_idx ON messages (chat_id);
CREATE INDEX IF NOT EXISTS messages_chat_id_created_at_idx ON messages (chat_id, created_at);
