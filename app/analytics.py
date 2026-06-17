from sqlalchemy import text
from app.db import engine


def get_top_stations(limit: int = 10) -> list[dict]:
    """Q1 — Stations avec le plus fort taux d'utilisation (dernière heure)."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT station_name, city, network_name,
                   ROUND(AVG(utilization_rate)::NUMERIC, 2) AS avg_utilization,
                   ROUND(AVG(bikes_available)::NUMERIC, 1)  AS avg_bikes,
                   latitude, longitude
            FROM analytics_station_snapshot
            WHERE ingested_at >= NOW() - INTERVAL '1 hour'
              AND total_capacity > 0
            GROUP BY station_id, station_name, city, network_name, latitude, longitude
            ORDER BY avg_utilization DESC
            LIMIT :limit
        """), {"limit": limit})
        return [dict(r._mapping) for r in rows]


def get_insufficient_supply_zones() -> list[dict]:
    """Q2 — Zones géographiques où l'offre de vélos est insuffisante."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT city,
                   ROUND(latitude::NUMERIC, 2)          AS lat_zone,
                   ROUND(longitude::NUMERIC, 2)         AS lon_zone,
                   COUNT(DISTINCT station_id)           AS nb_stations,
                   ROUND(AVG(bikes_available)::NUMERIC, 1) AS avg_bikes,
                   ROUND(AVG(utilization_rate)::NUMERIC, 1) AS avg_utilization,
                   SUM(CASE WHEN is_critical THEN 1 ELSE 0 END) AS nb_critiques
            FROM analytics_station_snapshot
            WHERE ingested_at >= NOW() - INTERVAL '1 hour'
            GROUP BY city, ROUND(latitude::NUMERIC, 2), ROUND(longitude::NUMERIC, 2)
            HAVING AVG(bikes_available) < 3
            ORDER BY avg_bikes ASC
            LIMIT 50
        """))
        return [dict(r._mapping) for r in rows]


def get_hourly_peak() -> list[dict]:
    """Q3 — Pics d'utilisation journaliers par tranche horaire."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT EXTRACT(HOUR FROM ingested_at)          AS hour_of_day,
                   ROUND(AVG(utilization_rate)::NUMERIC, 2) AS avg_utilization,
                   ROUND(AVG(bikes_available)::NUMERIC, 2)  AS avg_bikes,
                   ROUND(AVG(free_slots)::NUMERIC, 2)       AS avg_free_slots
            FROM station_snapshots
            WHERE ingested_at >= NOW() - INTERVAL '7 days'
            GROUP BY EXTRACT(HOUR FROM ingested_at)
            ORDER BY hour_of_day
        """))
        return [dict(r._mapping) for r in rows]


def get_geographic_balance() -> list[dict]:
    """Q4 — Déséquilibres géographiques de disponibilité des vélos."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT city,
                   ROUND(latitude::NUMERIC, 1)              AS lat_zone,
                   ROUND(longitude::NUMERIC, 1)             AS lon_zone,
                   COUNT(DISTINCT station_id)               AS nb_stations,
                   ROUND(AVG(bikes_available)::NUMERIC, 1)  AS avg_bikes,
                   ROUND(AVG(utilization_rate)::NUMERIC, 1) AS avg_utilization,
                   SUM(CASE WHEN is_critical THEN 1 ELSE 0 END) AS nb_critiques
            FROM analytics_station_snapshot
            WHERE ingested_at >= NOW() - INTERVAL '1 hour'
            GROUP BY city, ROUND(latitude::NUMERIC, 1), ROUND(longitude::NUMERIC, 1)
            ORDER BY city, avg_utilization DESC
            LIMIT 100
        """))
        return [dict(r._mapping) for r in rows]


def get_rebalancing_stations(limit: int = 20) -> list[dict]:
    """Q5 — Stations critiques nécessitant un rééquilibrage."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT station_name, city, network_name, latitude, longitude,
                   ROUND(
                       COUNT(*) FILTER (WHERE is_critical)::NUMERIC
                       / NULLIF(COUNT(*), 0) * 100, 1
                   )                                          AS critical_pct,
                   ROUND(AVG(bikes_available)::NUMERIC, 1)   AS avg_bikes,
                   COUNT(*) FILTER (WHERE status = 'empty')  AS nb_vides,
                   COUNT(*) FILTER (WHERE status = 'full')   AS nb_pleines
            FROM analytics_station_snapshot
            WHERE ingested_at >= NOW() - INTERVAL '1 hour'
            GROUP BY station_id, station_name, city, network_name, latitude, longitude
            HAVING COUNT(*) FILTER (WHERE is_critical) > 0
            ORDER BY critical_pct DESC
            LIMIT :limit
        """), {"limit": limit})
        return [dict(r._mapping) for r in rows]


def get_critical_stations() -> list[dict]:
    """Stations actuellement critiques (dernières 5 minutes)."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT ON (station_id)
                station_name, city, network_name,
                bikes_available, free_slots, status, ingested_at
            FROM analytics_station_snapshot
            WHERE is_critical = TRUE
              AND ingested_at >= NOW() - INTERVAL '5 minutes'
            ORDER BY station_id, ingested_at DESC
        """))
        return [dict(r._mapping) for r in rows]


def get_network_overview() -> list[dict]:
    """Vue d'ensemble par réseau — dernière heure (fallback si rien en 5 min)."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT n.network_id, n.network_name, n.city,
                   COUNT(DISTINCT ss.station_id)               AS total_stations,
                   SUM(ss.bikes_available)                     AS total_bikes_available,
                   SUM(ss.total_capacity)                      AS total_capacity,
                   ROUND(AVG(ss.utilization_rate)::NUMERIC, 1) AS avg_utilization,
                   SUM(CASE WHEN ss.is_critical THEN 1 ELSE 0 END) AS critical_stations,
                   MAX(ss.ingested_at)                         AS last_update
            FROM station_snapshots ss
            JOIN networks n ON n.network_id = ss.network_id
            WHERE ss.ingested_at >= NOW() - INTERVAL '1 hour'
            GROUP BY n.network_id, n.network_name, n.city
            ORDER BY n.city
        """))
        return [dict(r._mapping) for r in rows]
