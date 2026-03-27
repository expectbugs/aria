-- ARIA PostgreSQL schema
-- Run: psql -U aria aria < schema.sql

-- Calendar events
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    date DATE NOT NULL,
    time TIME,
    notes TEXT,
    created TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);

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

-- Monitor state (replaces monitor_state.json — all stores must use PostgreSQL)
CREATE TABLE IF NOT EXISTS monitor_state (
    key TEXT PRIMARY KEY,
    value REAL NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
