-- JARVIS v2 — Initial Schema
-- Phase 1: Sessions + Interaction Records (Brain Independence Phase A)

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Sessions
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    channel TEXT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_active TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_sessions_channel_user ON sessions(channel, user_id);

-- Interaction Records (Brain Independence — training data)
CREATE TABLE IF NOT EXISTS interaction_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID REFERENCES sessions(id),
    timestamp TIMESTAMPTZ DEFAULT NOW(),

    -- Input
    channel TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_message TEXT NOT NULL,

    -- Output
    model_used TEXT NOT NULL,
    model_response TEXT NOT NULL,
    reasoning_trace TEXT,
    confidence_score REAL DEFAULT 0,

    -- Metrics
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    cost_usd NUMERIC(10,6) DEFAULT 0,

    -- Feedback & Training
    user_feedback TEXT,          -- positive, negative, correction
    correction_text TEXT,
    quality_score REAL,
    selected_for_training BOOLEAN DEFAULT FALSE,
    training_batch TEXT
);

CREATE INDEX idx_interactions_session ON interaction_records(session_id);
CREATE INDEX idx_interactions_timestamp ON interaction_records(timestamp);
CREATE INDEX idx_interactions_training ON interaction_records(selected_for_training)
    WHERE selected_for_training = FALSE;
