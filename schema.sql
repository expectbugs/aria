-- ARIA PostgreSQL schema
-- Run: psql -U aria aria < schema.sql

-- Calendar events
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    date DATE NOT NULL,
    time TIME,
    notes TEXT,
    google_id TEXT,
    google_etag TEXT,
    last_synced TIMESTAMPTZ,
    created TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);
CREATE INDEX IF NOT EXISTS idx_events_google_id ON events(google_id);

-- Reminders
CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    due DATE,
    recurring TEXT,
    location TEXT,
    location_trigger TEXT,
    done BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at TIMESTAMPTZ,
    auto_expired_at TIMESTAMPTZ,
    created TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reminders_done ON reminders(done);

-- Health entries
CREATE TABLE IF NOT EXISTS health_entries (
    id TEXT PRIMARY KEY,
    date DATE NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    severity INTEGER,
    sleep_hours REAL,
    meal_type TEXT,
    content_hash TEXT,
    response_id TEXT,
    created TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_health_date ON health_entries(date);
CREATE INDEX IF NOT EXISTS idx_health_date_category ON health_entries(date, category);
CREATE UNIQUE INDEX IF NOT EXISTS idx_health_content_hash ON health_entries(content_hash);

-- Vehicle maintenance
CREATE TABLE IF NOT EXISTS vehicle_entries (
    id TEXT PRIMARY KEY,
    date DATE NOT NULL,
    event_type TEXT NOT NULL,
    description TEXT NOT NULL,
    mileage INTEGER,
    cost REAL,
    created TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Legal case log
CREATE TABLE IF NOT EXISTS legal_entries (
    id TEXT PRIMARY KEY,
    date DATE NOT NULL,
    entry_type TEXT NOT NULL,
    description TEXT NOT NULL,
    contacts TEXT[] NOT NULL DEFAULT '{}',
    created TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Timers
CREATE TABLE IF NOT EXISTS timers (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    fire_at TIMESTAMPTZ NOT NULL,
    delivery TEXT NOT NULL DEFAULT 'sms',
    priority TEXT NOT NULL DEFAULT 'gentle',
    message TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'user',
    status TEXT NOT NULL DEFAULT 'pending',
    created TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fired_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_timers_status_fire ON timers(status, fire_at);

-- Nutrition tracking
CREATE TABLE IF NOT EXISTS nutrition_entries (
    id TEXT PRIMARY KEY,
    date DATE NOT NULL,
    time TIME NOT NULL,
    meal_type TEXT NOT NULL,
    food_name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'label_photo',
    servings REAL NOT NULL DEFAULT 1.0,
    serving_size TEXT,
    nutrients JSONB NOT NULL DEFAULT '{}',
    notes TEXT,
    content_hash TEXT,
    response_id TEXT,
    created TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_nutrition_date ON nutrition_entries(date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_nutrition_content_hash ON nutrition_entries(content_hash);

-- Location tracking
CREATE TABLE IF NOT EXISTS locations (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    location TEXT,
    accuracy_m REAL,
    speed_mps REAL,
    battery_pct INTEGER
);
CREATE INDEX IF NOT EXISTS idx_locations_timestamp ON locations(timestamp);

-- Fitbit daily snapshots (JSONB blob per day)
CREATE TABLE IF NOT EXISTS fitbit_snapshots (
    date DATE PRIMARY KEY,
    data JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Fitbit exercise state
CREATE TABLE IF NOT EXISTS fitbit_exercise (
    id SERIAL PRIMARY KEY,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    exercise_type TEXT NOT NULL DEFAULT 'general',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    end_reason TEXT,
    resting_hr INTEGER,
    max_hr INTEGER,
    target_zones JSONB,
    hr_readings JSONB NOT NULL DEFAULT '[]',
    nudge_count INTEGER NOT NULL DEFAULT 0,
    summary JSONB
);
CREATE INDEX IF NOT EXISTS idx_exercise_active ON fitbit_exercise(active);

-- Email cache — full body + full-text search (Gmail integration)
CREATE TABLE IF NOT EXISTS email_cache (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    from_address TEXT NOT NULL,
    from_name TEXT,
    to_addresses TEXT,
    subject TEXT,
    snippet TEXT,
    body TEXT,
    body_search tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(subject, '') || ' ' || coalesce(body, ''))) STORED,
    labels TEXT[],
    has_attachments BOOLEAN DEFAULT FALSE,
    attachment_paths TEXT[],
    gmail_category TEXT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_email_cache_timestamp ON email_cache(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_email_cache_from ON email_cache(from_address);
CREATE INDEX IF NOT EXISTS idx_email_cache_thread ON email_cache(thread_id);
CREATE INDEX IF NOT EXISTS idx_email_body_search ON email_cache USING GIN(body_search);

-- Email classifications (training data + audit trail)
CREATE TABLE IF NOT EXISTS email_classifications (
    id SERIAL PRIMARY KEY,
    email_id TEXT NOT NULL REFERENCES email_cache(id),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tier TEXT NOT NULL,
    classification TEXT NOT NULL,
    confidence FLOAT,
    reason TEXT,
    category TEXT,
    user_override TEXT,
    surfaced BOOLEAN DEFAULT FALSE,
    acted_on BOOLEAN DEFAULT FALSE,
    surfaced_count INTEGER DEFAULT 0,
    last_surfaced TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_email_class_email ON email_classifications(email_id);

-- Email watches (temporary alerts for expected emails)
CREATE TABLE IF NOT EXISTS email_watches (
    id SERIAL PRIMARY KEY,
    sender_pattern TEXT,
    content_pattern TEXT,
    classification TEXT NOT NULL DEFAULT 'important',
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    fulfilled_at TIMESTAMPTZ,
    fulfilled_email_id TEXT,
    active BOOLEAN DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_email_watches_active ON email_watches(active) WHERE active = TRUE;

-- Calendar sync state (singleton row for incremental sync token)
CREATE TABLE IF NOT EXISTS calendar_sync_state (
    id INTEGER PRIMARY KEY DEFAULT 1,
    sync_token TEXT,
    last_full_sync TIMESTAMPTZ,
    last_incremental_sync TIMESTAMPTZ
);

-- Request log
CREATE TABLE IF NOT EXISTS request_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    input TEXT,
    status TEXT NOT NULL,
    response TEXT,
    error TEXT,
    duration_s REAL
);
CREATE INDEX IF NOT EXISTS idx_request_log_timestamp ON request_log(timestamp);

-- SMS conversation log
CREATE TABLE IF NOT EXISTS sms_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    from_number TEXT,
    to_number TEXT,
    inbound TEXT,
    media TEXT[],
    response TEXT,
    duration_s REAL
);

-- SMS outbound log
CREATE TABLE IF NOT EXISTS sms_outbound (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    to_number TEXT NOT NULL,
    body TEXT,
    media_url TEXT,
    sid TEXT
);

-- Tick state (key-value)
CREATE TABLE IF NOT EXISTS tick_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Nudge cooldowns
CREATE TABLE IF NOT EXISTS nudge_cooldowns (
    nudge_type TEXT PRIMARY KEY,
    last_fired TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Nudge audit log (global frequency cap + debugging)
CREATE TABLE IF NOT EXISTS nudge_log (
    id SERIAL PRIMARY KEY,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    nudge_types TEXT[] NOT NULL,
    trigger_descriptions TEXT[] NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    delivery_status TEXT NOT NULL DEFAULT 'sent'
);
CREATE INDEX IF NOT EXISTS idx_nudge_log_sent ON nudge_log(sent_at);

-- Webhook idempotency (prevents duplicate processing on Twilio retries)
CREATE TABLE IF NOT EXISTS processed_webhooks (
    message_sid TEXT PRIMARY KEY,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_processed_webhooks_at ON processed_webhooks(processed_at);

-- Monitor state (replaces monitor_state.json — all stores must use PostgreSQL)
CREATE TABLE IF NOT EXISTS monitor_state (
    key TEXT PRIMARY KEY,
    value REAL NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Tool usage traces (LoRA training data collection)
CREATE TABLE IF NOT EXISTS tool_traces (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    request_input TEXT,
    tool_name TEXT NOT NULL,
    tool_input TEXT,
    tool_output TEXT,
    was_correct BOOLEAN DEFAULT TRUE,
    correction TEXT
);
CREATE INDEX IF NOT EXISTS idx_tool_traces_timestamp ON tool_traces(timestamp);

-- Entity mentions (future Neo4j knowledge graph)
CREATE TABLE IF NOT EXISTS entity_mentions (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_value TEXT NOT NULL,
    context_snippet TEXT,
    source_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_entity_mentions_value ON entity_mentions(entity_value);

-- Interaction quality signals (LoRA preference training data)
CREATE TABLE IF NOT EXISTS interaction_quality (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    request_id INTEGER,
    quality_signal TEXT NOT NULL,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_interaction_quality_timestamp ON interaction_quality(timestamp);

-- Monitor findings (Phase 1 — domain monitors produce structured findings)
CREATE TABLE IF NOT EXISTS monitor_findings (
    id SERIAL PRIMARY KEY,
    domain TEXT NOT NULL,
    summary TEXT NOT NULL,
    urgency TEXT NOT NULL DEFAULT 'info',
    data JSONB,
    fingerprint TEXT NOT NULL,
    delivered BOOLEAN NOT NULL DEFAULT FALSE,
    delivered_at TIMESTAMPTZ,
    delivery_method TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_findings_undelivered ON monitor_findings(delivered, urgency);
CREATE INDEX IF NOT EXISTS idx_findings_fingerprint ON monitor_findings(fingerprint, delivered);
CREATE INDEX IF NOT EXISTS idx_findings_created ON monitor_findings(created_at);

-- Verification log (Phase 3 — LoRA training data for hallucination prevention)
CREATE TABLE IF NOT EXISTS verification_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    request_text TEXT,
    response_text TEXT,
    claim_text TEXT,
    claim_type TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    evidence TEXT,
    retry_attempt INTEGER NOT NULL DEFAULT 0,
    original_response TEXT
);
CREATE INDEX IF NOT EXISTS idx_verification_log_timestamp ON verification_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_verification_log_status ON verification_log(verification_status);

-- Delivery decision log (Phase 2)
CREATE TABLE IF NOT EXISTS delivery_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    content_type TEXT NOT NULL,
    source_channel TEXT,
    hint TEXT,
    chosen_method TEXT NOT NULL,
    reason TEXT,
    user_location TEXT,
    user_activity TEXT,
    delivered BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_delivery_log_timestamp ON delivery_log(timestamp);

-- Device state tracking (forward-looking for glasses/watch/mic)
CREATE TABLE IF NOT EXISTS device_state (
    device TEXT PRIMARY KEY,
    connected BOOLEAN NOT NULL DEFAULT FALSE,
    battery_pct INTEGER,
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    capabilities JSONB NOT NULL DEFAULT '{}'
);

-- Seed initial device state rows
INSERT INTO device_state (device, connected, capabilities) VALUES
    ('phone', FALSE, '{"voice_in": true, "voice_out": true, "display": true, "sms": true}'),
    ('glasses', FALSE, '{"display": true, "voice_in": true}'),
    ('watch', FALSE, '{"voice_in": true, "voice_out": true, "haptic": true}'),
    ('mic', FALSE, '{"voice_in": true, "ambient": true}')
ON CONFLICT (device) DO NOTHING;

-- Deferred delivery queue (Phase 2)
CREATE TABLE IF NOT EXISTS deferred_deliveries (
    id SERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    content_type TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal',
    source TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered BOOLEAN NOT NULL DEFAULT FALSE,
    delivered_at TIMESTAMPTZ,
    delivery_method TEXT,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deferred_undelivered ON deferred_deliveries(delivered, expires_at);

-- =========================================================================
-- Phase 6: Ambient Audio Pipeline (Total Recall)
-- =========================================================================

-- Individual utterances from continuous DJI Mic 3 capture
CREATE TABLE IF NOT EXISTS ambient_transcripts (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER,              -- FK to ambient_conversations (NULL until grouped)
    source TEXT NOT NULL,                  -- 'slappy', 'phone', 'beardos'
    speaker TEXT,                          -- diarization label or person name
    text TEXT NOT NULL,
    text_search tsvector
        GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    duration_s REAL,
    confidence REAL,                       -- Whisper language_probability
    quality_pass TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'done', 'skipped'
    quality_text TEXT,                     -- WhisperX refined text (NULL until quality pass)
    quality_speaker TEXT,                  -- diarization-resolved speaker
    audio_path TEXT,                       -- path to WAV chunk (NULL after retention cleanup)
    has_wake_word BOOLEAN NOT NULL DEFAULT FALSE,
    extracted BOOLEAN NOT NULL DEFAULT FALSE,  -- extraction pass completed
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ambient_started ON ambient_transcripts(started_at);
CREATE INDEX IF NOT EXISTS idx_ambient_conversation ON ambient_transcripts(conversation_id);
CREATE INDEX IF NOT EXISTS idx_ambient_quality ON ambient_transcripts(quality_pass)
    WHERE quality_pass = 'pending';
CREATE INDEX IF NOT EXISTS idx_ambient_text_search ON ambient_transcripts USING GIN(text_search);
CREATE INDEX IF NOT EXISTS idx_ambient_wake ON ambient_transcripts(has_wake_word)
    WHERE has_wake_word = TRUE;
CREATE INDEX IF NOT EXISTS idx_ambient_extracted ON ambient_transcripts(extracted)
    WHERE extracted = FALSE;

-- Grouped conversation segments (multiple transcripts form one conversation)
CREATE TABLE IF NOT EXISTS ambient_conversations (
    id SERIAL PRIMARY KEY,
    title TEXT,                            -- auto-generated summary title
    summary TEXT,                          -- Haiku-generated summary
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    duration_s REAL,
    segment_count INTEGER NOT NULL DEFAULT 0,
    speakers TEXT[] DEFAULT '{}',
    location TEXT,                         -- from location_store at conversation time
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conversations_started ON ambient_conversations(started_at);

-- FK from transcripts to conversations (deferred because conversations created after transcripts)
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_ambient_conversation'
    ) THEN
        ALTER TABLE ambient_transcripts
            ADD CONSTRAINT fk_ambient_conversation
            FOREIGN KEY (conversation_id) REFERENCES ambient_conversations(id)
            ON DELETE SET NULL;
    END IF;
END $$;

-- Commitments/promises extracted from conversations or direct interactions
CREATE TABLE IF NOT EXISTS commitments (
    id SERIAL PRIMARY KEY,
    source TEXT NOT NULL,                  -- 'ambient', 'direct', 'email'
    source_id INTEGER,                     -- ambient_transcripts.id or request_log.id
    conversation_id INTEGER REFERENCES ambient_conversations(id) ON DELETE SET NULL,
    who TEXT NOT NULL,                      -- who made the commitment
    what TEXT NOT NULL,                     -- the commitment itself
    to_whom TEXT,                           -- who it was made to
    due_date DATE,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'done', 'cancelled', 'expired')),
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_commitments_status ON commitments(status)
    WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_commitments_who ON commitments(who);
CREATE INDEX IF NOT EXISTS idx_commitments_due ON commitments(due_date)
    WHERE due_date IS NOT NULL AND status = 'open';

-- Person profiles built from conversation mentions
CREATE TABLE IF NOT EXISTS person_profiles (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    aliases TEXT[] DEFAULT '{}',
    relationship TEXT,                     -- 'coworker', 'friend', 'family', etc.
    organization TEXT,
    notes TEXT,
    last_mentioned TIMESTAMPTZ,
    mention_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_person_name ON person_profiles(name);

-- Daily narrative summaries of ambient conversations
CREATE TABLE IF NOT EXISTS daily_summaries (
    date DATE PRIMARY KEY,
    summary TEXT NOT NULL,
    key_topics TEXT[] DEFAULT '{}',
    people_mentioned TEXT[] DEFAULT '{}',
    commitments_made INTEGER NOT NULL DEFAULT 0,
    conversation_count INTEGER NOT NULL DEFAULT 0,
    total_duration_s REAL NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
