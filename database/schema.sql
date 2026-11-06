CREATE TABLE IF NOT EXISTS snapshots (
    id uuid PRIMARY KEY,
    collected_at timestamptz NOT NULL,
    market_timestamp text NOT NULL,
    symbol text NOT NULL DEFAULT 'NIFTY',
    expiry date NOT NULL,
    spot numeric NOT NULL CHECK (spot > 0),
    index_rsi numeric,
    raw_payload jsonb NOT NULL,
    warnings jsonb NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS snapshots_time_idx ON snapshots (collected_at DESC, id);
CREATE TABLE IF NOT EXISTS snapshot_rows (
    snapshot_id uuid REFERENCES snapshots(id) ON DELETE CASCADE,
    strike numeric NOT NULL,
    expiry date NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (snapshot_id, strike, expiry)
);
CREATE TABLE IF NOT EXISTS option_quotes (
    snapshot_id uuid REFERENCES snapshots(id) ON DELETE CASCADE,
    strike numeric NOT NULL,
    expiry date NOT NULL,
    side text NOT NULL CHECK (side IN ('CE', 'PE')),
    last_price numeric,
    open_interest bigint,
    volume bigint,
    implied_volatility numeric,
    rsi_15m numeric,
    details jsonb NOT NULL,
    PRIMARY KEY (snapshot_id, strike, expiry, side)
);
CREATE INDEX IF NOT EXISTS quotes_contract_idx ON option_quotes (expiry, strike, side, snapshot_id);
CREATE TABLE IF NOT EXISTS trading_calendars (
    year integer PRIMARY KEY,
    holidays jsonb NOT NULL,
    source text NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS collection_attempts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    snapshot_id uuid REFERENCES snapshots(id),
    error text
);
CREATE TABLE IF NOT EXISTS collector_state (
    id integer PRIMARY KEY CHECK (id=1),
    payload jsonb NOT NULL DEFAULT '{}',
    heartbeat timestamptz NOT NULL DEFAULT now()
);
INSERT INTO collector_state(id) VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS refresh_requests (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    requested_at timestamptz NOT NULL DEFAULT now(),
    handled_at timestamptz
);
