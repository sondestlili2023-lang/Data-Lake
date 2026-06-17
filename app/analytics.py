from sqlalchemy import text
from app.db import engine

# Sous-requête réutilisable : borne basse = dernière ingestion - fenêtre
# Fonctionne même si l'ingestion a du retard (rate limit, panne réseau, etc.)
_LAST = "(SELECT MAX(ingested_at) FROM station_snapshots)"


def get_top_stations(limit: int = 10) -> list[dict]:
    """Q1 — Stations avec le plus fort taux d'utilisation (dernier batch)."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT station_name, city, network_name,
                   ROUND(AVG(utilization_rate)::NUMERIC, 2) AS avg_utilization,
                   ROUND(AVG(bikes_available)::NUMERIC, 1)  AS avg_bikes,
                   latitude, longitude
            FROM analytics_station_snapshot
            WHERE ingested_at >= NOW() - INTERVAL '72 hour'
              AND total_capacity > 0
            GROUP BY station_id, station_name, city, network_name, latitude, longitude
            ORDER BY avg_utilization DESC
            LIMIT :limit
        """), {"limit": limit})
        return [dict(r._mapping) for r in rows]


def get_insufficient_supply_zones() -> list[dict]:
    """Q2 — Zones géographiques où l'offre de vélos est insuffisante."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT city,
                   ROUND(latitude::NUMERIC, 2)               AS lat_zone,
                   ROUND(longitude::NUMERIC, 2)              AS lon_zone,
                   COUNT(DISTINCT station_id)                AS nb_stations,
                   ROUND(AVG(bikes_available)::NUMERIC, 1)   AS avg_bikes,
                   ROUND(AVG(utilization_rate)::NUMERIC, 1)  AS avg_utilization,
                   SUM(CASE WHEN is_critical THEN 1 ELSE 0 END) AS nb_critiques
            FROM analytics_station_snapshot
            WHERE ingested_at >= {_LAST} - INTERVAL '30 minutes'
            GROUP BY city, ROUND(latitude::NUMERIC, 2), ROUND(longitude::NUMERIC, 2)
            HAVING AVG(bikes_available) < 3
            ORDER BY avg_bikes ASC
            LIMIT 50
        """))
        return [dict(r._mapping) for r in rows]


def get_hourly_peak() -> list[dict]:
    """Q3 — Pics d'utilisation journaliers par tranche horaire (7 jours)."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT EXTRACT(HOUR FROM ingested_at)           AS hour_of_day,
                   ROUND(AVG(utilization_rate)::NUMERIC, 2) AS avg_utilization,
                   ROUND(AVG(bikes_available)::NUMERIC, 2)  AS avg_bikes,
                   ROUND(AVG(free_slots)::NUMERIC, 2)       AS avg_free_slots
            FROM station_snapshots
            WHERE ingested_at >= {_LAST} - INTERVAL '7 days'
            GROUP BY EXTRACT(HOUR FROM ingested_at)
            ORDER BY hour_of_day
        """))
        return [dict(r._mapping) for r in rows]


def get_geographic_balance() -> list[dict]:
    """Q4 — Déséquilibres géographiques de disponibilité des vélos."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT city,
                   ROUND(latitude::NUMERIC, 1)              AS lat_zone,
                   ROUND(longitude::NUMERIC, 1)             AS lon_zone,
                   COUNT(DISTINCT station_id)               AS nb_stations,
                   ROUND(AVG(bikes_available)::NUMERIC, 1)  AS avg_bikes,
                   ROUND(AVG(utilization_rate)::NUMERIC, 1) AS avg_utilization,
                   SUM(CASE WHEN is_critical THEN 1 ELSE 0 END) AS nb_critiques
            FROM analytics_station_snapshot
            WHERE ingested_at >= {_LAST} - INTERVAL '30 minutes'
            GROUP BY city, ROUND(latitude::NUMERIC, 1), ROUND(longitude::NUMERIC, 1)
            ORDER BY city, avg_utilization DESC
            LIMIT 100
        """))
        return [dict(r._mapping) for r in rows]


def get_rebalancing_stations(limit: int = 20) -> list[dict]:
    """Q5 — Stations critiques nécessitant un rééquilibrage."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT station_name, city, network_name, latitude, longitude,
                   ROUND(
                       COUNT(*) FILTER (WHERE is_critical)::NUMERIC
                       / NULLIF(COUNT(*), 0) * 100, 1
                   )                                         AS critical_pct,
                   ROUND(AVG(bikes_available)::NUMERIC, 1)  AS avg_bikes,
                   COUNT(*) FILTER (WHERE status = 'empty') AS nb_vides,
                   COUNT(*) FILTER (WHERE status = 'full')  AS nb_pleines
            FROM analytics_station_snapshot
            WHERE ingested_at >= {_LAST} - INTERVAL '30 minutes'
            GROUP BY station_id, station_name, city, network_name, latitude, longitude
            HAVING COUNT(*) FILTER (WHERE is_critical) > 0
            ORDER BY critical_pct DESC
            LIMIT :limit
        """), {"limit": limit})
        return [dict(r._mapping) for r in rows]


def get_critical_stations() -> list[dict]:
    """Stations actuellement critiques (dernier batch disponible)."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT DISTINCT ON (station_id)
                station_name, city, network_name,
                bikes_available, free_slots, status, ingested_at,
                latitude, longitude
            FROM analytics_station_snapshot
            WHERE is_critical = TRUE
              AND ingested_at >= {_LAST} - INTERVAL '30 minutes'
            ORDER BY station_id, ingested_at DESC
        """))
        return [dict(r._mapping) for r in rows]


def get_station_reliability() -> list[dict]:
    """KPI Direction — Taux de fiabilité par station (pires stations en priorité)."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT station_name, city,
                   COUNT(*)                                                   AS nb_mesures,
                   ROUND(COUNT(*) FILTER (WHERE status = 'ok')::NUMERIC
                         / NULLIF(COUNT(*), 0) * 100, 1)                      AS taux_fiabilite,
                   COUNT(*) FILTER (WHERE status = 'empty')                   AS nb_vides,
                   COUNT(*) FILTER (WHERE status = 'full')                    AS nb_pleines,
                   ROUND(AVG(bikes_available)::NUMERIC, 1)                    AS velos_moy
            FROM analytics_station_snapshot
            WHERE ingested_at >= {_LAST} - INTERVAL '6 hours'
            GROUP BY station_id, station_name, city
            HAVING COUNT(*) >= 3
            ORDER BY taux_fiabilite ASC
            LIMIT 20
        """))
        return [dict(r._mapping) for r in rows]


def get_ghost_stations() -> list[dict]:
    """KPI Direction — Stations fantômes : toujours vides ou pleines (bloquées)."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT station_name, city,
                   MAX(status)              AS statut_bloque,
                   COUNT(*)                 AS nb_mesures,
                   MIN(ingested_at)         AS bloquee_depuis,
                   AVG(bikes_available)::INTEGER AS velos
            FROM analytics_station_snapshot
            WHERE ingested_at >= {_LAST} - INTERVAL '6 hours'
            GROUP BY station_id, station_name, city
            HAVING COUNT(DISTINCT status) = 1
               AND MAX(status) IN ('empty', 'full')
               AND COUNT(*) >= 3
            ORDER BY nb_mesures DESC
            LIMIT 20
        """))
        return [dict(r._mapping) for r in rows]


def get_city_efficiency() -> list[dict]:
    """KPI Direction — Efficacité réseau par ville (disponibilité réelle des vélos)."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT city,
                   COUNT(DISTINCT station_id)                             AS nb_stations,
                   SUM(bikes_available)                                   AS total_velos,
                   SUM(total_capacity)                                    AS total_capacite,
                   ROUND(SUM(bikes_available)::NUMERIC
                         / NULLIF(SUM(total_capacity), 0) * 100, 1)       AS taux_disponibilite,
                   SUM(CASE WHEN is_critical THEN 1 ELSE 0 END)           AS stations_critiques,
                   ROUND(SUM(CASE WHEN is_critical THEN 1 ELSE 0 END)::NUMERIC
                         / NULLIF(COUNT(*), 0) * 100, 1)                  AS pct_critique
            FROM analytics_station_snapshot
            WHERE ingested_at >= {_LAST} - INTERVAL '6 hours'
            GROUP BY city
            HAVING COUNT(DISTINCT station_id) >= 3
            ORDER BY taux_disponibilite ASC
            LIMIT 30
        """))
        return [dict(r._mapping) for r in rows]


def get_hourly_criticality() -> list[dict]:
    """KPI Direction — % de stations en état critique par heure (planification équipes)."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT EXTRACT(HOUR FROM ingested_at)                          AS heure,
                   COUNT(*)                                                AS nb_mesures,
                   ROUND(COUNT(*) FILTER (WHERE is_critical)::NUMERIC
                         / NULLIF(COUNT(*), 0) * 100, 1)                  AS pct_critique,
                   COUNT(*) FILTER (WHERE status = 'empty')               AS nb_vides,
                   COUNT(*) FILTER (WHERE status = 'full')                AS nb_pleines
            FROM station_snapshots
            WHERE ingested_at >= {_LAST} - INTERVAL '7 days'
            GROUP BY EXTRACT(HOUR FROM ingested_at)
            ORDER BY heure
        """))
        return [dict(r._mapping) for r in rows]


def get_network_overview() -> list[dict]:
    """Vue d'ensemble par réseau — dernier batch disponible."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT n.network_id, n.network_name, n.city,
                   COUNT(DISTINCT ss.station_id)               AS total_stations,
                   SUM(ss.bikes_available)                     AS total_bikes_available,
                   SUM(ss.total_capacity)                      AS total_capacity,
                   ROUND(AVG(ss.utilization_rate)::NUMERIC, 1) AS avg_utilization,
                   SUM(CASE WHEN ss.is_critical THEN 1 ELSE 0 END) AS critical_stations,
                   MAX(ss.ingested_at)                         AS last_update
            FROM station_snapshots ss
            JOIN networks n ON n.network_id = ss.network_id
            WHERE ss.ingested_at >= {_LAST} - INTERVAL '30 minutes'
            GROUP BY n.network_id, n.network_name, n.city
            ORDER BY n.city
        """))
        return [dict(r._mapping) for r in rows]
