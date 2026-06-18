-- Migration pour bases existantes (idempotent)
ALTER TABLE station_snapshots ADD COLUMN IF NOT EXISTS bikes_mechanical INT DEFAULT 0;
ALTER TABLE station_snapshots ADD COLUMN IF NOT EXISTS bikes_ebike INT DEFAULT 0;
ALTER TABLE station_snapshots ADD COLUMN IF NOT EXISTS is_installed BOOLEAN DEFAULT TRUE;
ALTER TABLE station_snapshots ADD COLUMN IF NOT EXISTS is_renting BOOLEAN DEFAULT TRUE;
ALTER TABLE station_snapshots ADD COLUMN IF NOT EXISTS is_returning BOOLEAN DEFAULT TRUE;
ALTER TABLE station_snapshots ADD COLUMN IF NOT EXISTS last_reported TIMESTAMP;

CREATE TABLE IF NOT EXISTS alert_recipients (
    id         SERIAL PRIMARY KEY,
    phone_e164 TEXT NOT NULL UNIQUE,
    label      TEXT,
    channel    TEXT DEFAULT 'whatsapp',
    active     BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS alert_snooze (
    contact_key  TEXT PRIMARY KEY,
    channel      TEXT NOT NULL,
    paused_until TIMESTAMP NOT NULL,
    paused_at    TIMESTAMP DEFAULT NOW()
);

CREATE OR REPLACE VIEW analytics_station_snapshot AS
SELECT
    ss.snapshot_id,
    ss.station_id,
    ss.station_name,
    n.network_id,
    n.network_name,
    n.city,
    ss.latitude,
    ss.longitude,
    ss.bikes_available,
    ss.free_slots,
    ss.total_capacity,
    ss.utilization_rate,
    ss.is_critical,
    ss.status,
    ss.bikes_mechanical,
    ss.bikes_ebike,
    ss.is_installed,
    ss.is_renting,
    ss.is_returning,
    ss.last_reported,
    ss.timestamp,
    ss.ingested_at
FROM station_snapshots ss
JOIN networks n ON n.network_id = ss.network_id;
