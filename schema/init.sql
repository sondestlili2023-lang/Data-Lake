-- ============================================================
-- Vélib Data Lakehouse — schéma PostgreSQL
-- Exécuté par Docker au premier démarrage (après 00_setup.sh)
-- ============================================================

-- ── Tables de référence ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS networks (
    network_id   TEXT PRIMARY KEY,
    network_name TEXT NOT NULL,
    city         TEXT,
    country      TEXT,
    created_at   TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS stations (
    station_id   TEXT PRIMARY KEY,
    network_id   TEXT REFERENCES networks(network_id),
    station_name TEXT NOT NULL,
    latitude     DOUBLE PRECISION,
    longitude    DOUBLE PRECISION,
    created_at   TIMESTAMP DEFAULT NOW()
);

-- ── Table de faits historisée (1 ligne / station / minute) ───

CREATE TABLE IF NOT EXISTS station_snapshots (
    snapshot_id      BIGSERIAL PRIMARY KEY,
    station_id       TEXT NOT NULL,
    network_id       TEXT NOT NULL,
    station_name     TEXT,
    latitude         DOUBLE PRECISION,
    longitude        DOUBLE PRECISION,
    bikes_available  INT            DEFAULT 0,
    free_slots       INT            DEFAULT 0,
    total_capacity   INT            DEFAULT 0,
    utilization_rate NUMERIC(5,2)   DEFAULT 0,
    is_critical      BOOLEAN        DEFAULT FALSE,
    status           TEXT           DEFAULT 'ok',   -- 'empty' | 'full' | 'ok'
    timestamp        TIMESTAMP      NOT NULL,
    ingested_at      TIMESTAMP      DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ss_ingested     ON station_snapshots (ingested_at DESC);
CREATE INDEX IF NOT EXISTS idx_ss_station_time ON station_snapshots (station_id, ingested_at DESC);
CREATE INDEX IF NOT EXISTS idx_ss_network_time ON station_snapshots (network_id, ingested_at DESC);
CREATE INDEX IF NOT EXISTS idx_ss_critical     ON station_snapshots (is_critical, ingested_at DESC);

-- ── Vue de base (join networks) ─────────────────────────────

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
    ss.timestamp,
    ss.ingested_at
FROM station_snapshots ss
JOIN networks n ON n.network_id = ss.network_id;

-- ── Q1 — Stations avec le plus fort taux d'utilisation ──────

CREATE OR REPLACE VIEW v_top_utilized_stations AS
SELECT
    station_id,
    station_name,
    city,
    network_name,
    latitude,
    longitude,
    ROUND(AVG(utilization_rate)::NUMERIC, 2)  AS avg_utilization,
    ROUND(AVG(bikes_available)::NUMERIC, 1)   AS avg_bikes,
    COUNT(*)                                   AS nb_observations
FROM analytics_station_snapshot
WHERE ingested_at >= NOW() - INTERVAL '1 hour'
  AND total_capacity > 0
GROUP BY station_id, station_name, city, network_name, latitude, longitude
ORDER BY avg_utilization DESC;

-- ── Q2 — Zones géographiques où l'offre est insuffisante ─────

CREATE OR REPLACE VIEW v_insufficient_supply_zones AS
SELECT
    city,
    ROUND(latitude::NUMERIC,  2) AS lat_zone,
    ROUND(longitude::NUMERIC, 2) AS lon_zone,
    COUNT(DISTINCT station_id)                   AS nb_stations,
    ROUND(AVG(bikes_available)::NUMERIC, 1)      AS avg_bikes,
    ROUND(AVG(utilization_rate)::NUMERIC, 1)     AS avg_utilization,
    SUM(CASE WHEN is_critical THEN 1 ELSE 0 END) AS nb_critiques,
    ROUND(
        SUM(CASE WHEN is_critical THEN 1 ELSE 0 END)::NUMERIC
        / NULLIF(COUNT(*), 0) * 100, 1
    ) AS critical_pct
FROM analytics_station_snapshot
WHERE ingested_at >= NOW() - INTERVAL '1 hour'
GROUP BY city, ROUND(latitude::NUMERIC, 2), ROUND(longitude::NUMERIC, 2)
HAVING AVG(bikes_available) < 3
ORDER BY avg_bikes ASC;

-- ── Q3 — Pics d'utilisation journaliers par heure ────────────

CREATE OR REPLACE VIEW v_hourly_peaks AS
SELECT
    city,
    network_name,
    EXTRACT(HOUR FROM ingested_at)             AS hour_of_day,
    ROUND(AVG(utilization_rate)::NUMERIC, 2)   AS avg_utilization,
    ROUND(AVG(bikes_available)::NUMERIC, 1)    AS avg_bikes,
    COUNT(*)                                    AS nb_observations
FROM analytics_station_snapshot
WHERE ingested_at >= NOW() - INTERVAL '7 days'
GROUP BY city, network_name, EXTRACT(HOUR FROM ingested_at)
ORDER BY city, hour_of_day;

-- ── Q4 — Déséquilibres géographiques de disponibilité ────────

CREATE OR REPLACE VIEW v_geographic_balance AS
SELECT
    city,
    ROUND(latitude::NUMERIC,  1) AS lat_zone,
    ROUND(longitude::NUMERIC, 1) AS lon_zone,
    COUNT(DISTINCT station_id)                   AS nb_stations,
    ROUND(AVG(bikes_available)::NUMERIC, 1)      AS avg_bikes,
    ROUND(AVG(utilization_rate)::NUMERIC, 1)     AS avg_utilization,
    SUM(CASE WHEN is_critical THEN 1 ELSE 0 END) AS nb_critiques
FROM analytics_station_snapshot
WHERE ingested_at >= NOW() - INTERVAL '1 hour'
GROUP BY city, ROUND(latitude::NUMERIC, 1), ROUND(longitude::NUMERIC, 1)
ORDER BY city, avg_utilization DESC;

-- ── Q5 — Stations critiques nécessitant un rééquilibrage ─────

CREATE OR REPLACE VIEW v_stations_to_rebalance AS
SELECT
    station_id,
    station_name,
    city,
    network_name,
    latitude,
    longitude,
    ROUND(
        COUNT(*) FILTER (WHERE is_critical)::NUMERIC
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                              AS critical_pct,
    ROUND(AVG(bikes_available)::NUMERIC, 1)        AS avg_bikes,
    COUNT(*) FILTER (WHERE status = 'empty')       AS nb_vides,
    COUNT(*) FILTER (WHERE status = 'full')        AS nb_pleines
FROM analytics_station_snapshot
WHERE ingested_at >= NOW() - INTERVAL '1 hour'
GROUP BY station_id, station_name, city, network_name, latitude, longitude
HAVING COUNT(*) FILTER (WHERE is_critical) > 0
ORDER BY critical_pct DESC;

-- ── Vue réseau (état courant) ─────────────────────────────────

CREATE OR REPLACE VIEW v_network_overview AS
SELECT
    n.network_id,
    n.network_name,
    n.city,
    COUNT(DISTINCT ss.station_id)                AS nb_stations,
    SUM(ss.bikes_available)                      AS total_bikes,
    SUM(ss.total_capacity)                       AS total_capacity,
    ROUND(AVG(ss.utilization_rate)::NUMERIC, 1)  AS avg_utilization,
    SUM(CASE WHEN ss.is_critical THEN 1 ELSE 0 END) AS nb_critiques,
    MAX(ss.ingested_at)                          AS last_update
FROM station_snapshots ss
JOIN networks n ON n.network_id = ss.network_id
WHERE ss.ingested_at >= NOW() - INTERVAL '5 minutes'
GROUP BY n.network_id, n.network_name, n.city
ORDER BY n.city;

-- ── Vue résumé horaire ────────────────────────────────────────

CREATE OR REPLACE VIEW analytics_hourly_summary AS
SELECT
    DATE_TRUNC('hour', ingested_at)              AS hour_bucket,
    COUNT(*)                                     AS snapshot_count,
    ROUND(AVG(bikes_available)::NUMERIC, 2)      AS avg_bikes_available,
    ROUND(AVG(free_slots)::NUMERIC, 2)           AS avg_free_slots,
    ROUND(AVG(utilization_rate)::NUMERIC, 2)     AS avg_utilization
FROM station_snapshots
GROUP BY DATE_TRUNC('hour', ingested_at)
ORDER BY hour_bucket DESC;
