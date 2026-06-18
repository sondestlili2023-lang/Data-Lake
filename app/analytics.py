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


_LATEST_SNAPSHOTS_CTE = """
    WITH latest AS (
        SELECT DISTINCT ON (ss.station_id)
            ss.station_id, ss.station_name, ss.network_id,
            ss.latitude, ss.longitude,
            ss.bikes_available, ss.free_slots, ss.total_capacity,
            ss.bikes_mechanical, ss.bikes_ebike,
            ss.status, ss.is_critical,
            ss.is_installed, ss.is_renting, ss.is_returning,
            ss.last_reported, ss.ingested_at
        FROM station_snapshots ss
        WHERE ss.ingested_at >= NOW() - INTERVAL '30 minutes'
          AND ss.network_id = 'velib-paris'
        ORDER BY ss.station_id, ss.ingested_at DESC
    )
"""


def get_executive_summary() -> dict:
    """Résumé exécutif — score service, flotte, stations à traiter, fraîcheur."""
    with engine.connect() as conn:
        row = conn.execute(text(f"""
            {_LATEST_SNAPSHOTS_CTE}
            SELECT
                COUNT(*)                                              AS total_stations,
                COUNT(*) FILTER (WHERE status = 'ok')                 AS stations_ok,
                COUNT(*) FILTER (WHERE status = 'empty')              AS stations_empty,
                COUNT(*) FILTER (WHERE status = 'full')               AS stations_full,
                COUNT(*) FILTER (WHERE status = 'closed')             AS stations_closed,
                SUM(bikes_available)                                  AS total_bikes,
                SUM(total_capacity)                                   AS total_capacity,
                SUM(bikes_mechanical)                                 AS total_mechanical,
                SUM(bikes_ebike)                                      AS total_ebike,
                MAX(last_reported)                                    AS last_reported,
                MAX(ingested_at)                                      AS last_ingested
            FROM latest
        """)).fetchone()

    if not row or not row.total_stations:
        return {
            "total_stations": 0,
            "service_score_pct": 0.0,
            "stations_to_treat": 0,
            "stations_ok": 0,
            "stations_empty": 0,
            "stations_full": 0,
            "stations_closed": 0,
            "total_bikes": 0,
            "total_capacity": 0,
            "fleet_availability_pct": 0.0,
            "ebike_share_pct": 0.0,
            "last_reported": None,
            "last_ingested": None,
        }

    total = int(row.total_stations)
    ok = int(row.stations_ok or 0)
    empty = int(row.stations_empty or 0)
    full = int(row.stations_full or 0)
    closed = int(row.stations_closed or 0)
    bikes = int(row.total_bikes or 0)
    capacity = int(row.total_capacity or 0)
    mech = int(row.total_mechanical or 0)
    ebike = int(row.total_ebike or 0)
    fleet_total = mech + ebike

    return {
        "total_stations": total,
        "service_score_pct": round(ok / total * 100, 1) if total else 0.0,
        "stations_to_treat": empty + full + closed,
        "stations_ok": ok,
        "stations_empty": empty,
        "stations_full": full,
        "stations_closed": closed,
        "total_bikes": bikes,
        "total_capacity": capacity,
        "fleet_availability_pct": round(bikes / capacity * 100, 1) if capacity else 0.0,
        "ebike_share_pct": round(ebike / fleet_total * 100, 1) if fleet_total else 0.0,
        "last_reported": row.last_reported.isoformat() if row.last_reported else None,
        "last_ingested": row.last_ingested.isoformat() if row.last_ingested else None,
    }


def get_fleet_mix() -> dict:
    """Totaux mécanique / VAE et part VAE dans la flotte disponible."""
    with engine.connect() as conn:
        row = conn.execute(text(f"""
            {_LATEST_SNAPSHOTS_CTE}
            SELECT
                SUM(bikes_mechanical) AS mechanical,
                SUM(bikes_ebike)      AS ebike,
                SUM(free_slots)       AS free_slots,
                SUM(bikes_available)  AS bikes_available
            FROM latest
        """)).fetchone()

    mech = int(row.mechanical or 0) if row else 0
    ebike = int(row.ebike or 0) if row else 0
    fleet = mech + ebike
    return {
        "mechanical": mech,
        "ebike": ebike,
        "free_slots": int(row.free_slots or 0) if row else 0,
        "bikes_available": int(row.bikes_available or 0) if row else 0,
        "ebike_share_pct": round(ebike / fleet * 100, 1) if fleet else 0.0,
    }


def get_priority_stations(limit: int = 15) -> list[dict]:
    """Stations urgentes avec action opérationnelle recommandée."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            {_LATEST_SNAPSHOTS_CTE}
            SELECT
                station_name,
                latitude,
                longitude,
                status,
                bikes_available,
                bikes_ebike,
                free_slots,
                CASE status
                    WHEN 'empty'   THEN 'REAPPRO'
                    WHEN 'full'    THEN 'VIDER'
                    WHEN 'closed'  THEN 'VERIFIER'
                    ELSE 'SURVEILLER'
                END AS action_label,
                CASE status
                    WHEN 'empty'   THEN 'Réapprovisionner'
                    WHEN 'full'    THEN 'Vider'
                    WHEN 'closed'  THEN 'Vérifier'
                    ELSE 'Surveiller'
                END AS action_text
            FROM latest
            WHERE status IN ('empty', 'full', 'closed')
            ORDER BY
                CASE status WHEN 'empty' THEN 1 WHEN 'full' THEN 2 ELSE 3 END,
                bikes_available ASC,
                free_slots DESC
            LIMIT :limit
        """), {"limit": limit})
        return [dict(r._mapping) for r in rows]
