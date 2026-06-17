import logging
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests
from sqlalchemy import text

from app.config import CITYBIKES_API_URL
from app.db import engine
from app.minio_client import BUCKETS, build_key, upload_bytes, upload_json

logger = logging.getLogger(__name__)

_NETWORKS_CACHE: list[dict] = []
_NETWORKS_CACHE_TS: float = 0.0
_NETWORKS_CACHE_TTL = 600  # 10 min


# ── API ──────────────────────────────────────────────────────────────────────

def _fetch(url: str, retries: int = 3) -> dict[str, Any]:
    delay = 10
    for attempt in range(retries):
        r = requests.get(url, timeout=30)
        if r.status_code == 429:
            logger.warning("429 rate limit — attente %ds (tentative %d/%d)", delay, attempt + 1, retries)
            time.sleep(delay)
            delay *= 2
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Echec après {retries} tentatives : {url}")


def get_france_networks() -> list[dict[str, Any]]:
    global _NETWORKS_CACHE, _NETWORKS_CACHE_TS
    if time.time() - _NETWORKS_CACHE_TS < _NETWORKS_CACHE_TTL and _NETWORKS_CACHE:
        return _NETWORKS_CACHE
    data = _fetch(f"{CITYBIKES_API_URL}/networks")
    _NETWORKS_CACHE = [n for n in data.get("networks", []) if n.get("location", {}).get("country") == "FR"]
    _NETWORKS_CACHE_TS = time.time()
    logger.info("Cache réseaux mis à jour : %d réseaux FR", len(_NETWORKS_CACHE))
    return _NETWORKS_CACHE


def fetch_network_details(network_id: str) -> dict[str, Any]:
    return _fetch(f"{CITYBIKES_API_URL}/networks/{network_id}")


# ── Normalisation ─────────────────────────────────────────────────────────────

def _normalize_station(network: dict, station: dict, ts: str) -> dict[str, Any]:
    """
    CityBikes API:
      free_bikes  → vélos disponibles à emprunter
      empty_slots → places libres pour rendre un vélo
    """
    bikes = int(station.get("free_bikes") or 0)
    slots = int(station.get("empty_slots") or 0)
    capacity = bikes + slots

    if capacity > 0:
        utilization_rate = round((1 - bikes / capacity) * 100, 2)
    else:
        utilization_rate = 0.0

    if bikes == 0:
        status = "empty"
    elif slots == 0:
        status = "full"
    else:
        status = "ok"

    loc = network.get("location", {})
    return {
        "station_id":       station.get("id", ""),
        "network_id":       network.get("id", ""),
        "network_name":     network.get("name", ""),
        "station_name":     station.get("name", ""),
        "latitude":         station.get("latitude"),
        "longitude":        station.get("longitude"),
        "city":             loc.get("city", ""),
        "country":          loc.get("country", "FR"),
        "bikes_available":  bikes,
        "free_slots":       slots,
        "total_capacity":   capacity,
        "utilization_rate": utilization_rate,
        "is_critical":      status in ("empty", "full"),
        "status":           status,
        "timestamp":        ts,
    }


# ── Agrégation curated ────────────────────────────────────────────────────────

def _compute_curated(records: list[dict], ts: str) -> dict:
    by_network: dict[str, dict] = {}
    for r in records:
        nid = r["network_id"]
        if nid not in by_network:
            by_network[nid] = {
                "network_id":    nid,
                "network_name":  r["network_name"],
                "city":          r["city"],
                "nb_stations":   0,
                "total_bikes":   0,
                "total_capacity": 0,
                "nb_critiques":  0,
                "nb_vides":      0,
                "nb_pleines":    0,
            }
        e = by_network[nid]
        e["nb_stations"]    += 1
        e["total_bikes"]    += r["bikes_available"]
        e["total_capacity"] += r["total_capacity"]
        e["nb_critiques"]   += int(r["is_critical"])
        e["nb_vides"]       += int(r["status"] == "empty")
        e["nb_pleines"]     += int(r["status"] == "full")

    for e in by_network.values():
        cap = e["total_capacity"]
        bikes = e["total_bikes"]
        e["utilization_rate"] = round((1 - bikes / cap) * 100, 2) if cap > 0 else 0
        e["critical_rate"]    = round(e["nb_critiques"] / e["nb_stations"] * 100, 2) if e["nb_stations"] > 0 else 0

    return {"timestamp": ts, "networks": list(by_network.values())}


# ── PostgreSQL ────────────────────────────────────────────────────────────────

def _upsert_network(network: dict) -> None:
    loc = network.get("location", {})
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO networks (network_id, network_name, city, country)
            VALUES (:network_id, :network_name, :city, :country)
            ON CONFLICT (network_id) DO UPDATE
            SET network_name = EXCLUDED.network_name,
                city         = EXCLUDED.city
        """), {
            "network_id":   network.get("id", ""),
            "network_name": network.get("name", ""),
            "city":         loc.get("city", ""),
            "country":      loc.get("country", "FR"),
        })


def _upsert_stations(records: list[dict]) -> None:
    with engine.begin() as conn:
        for r in records:
            conn.execute(text("""
                INSERT INTO stations (station_id, network_id, station_name, latitude, longitude)
                VALUES (:station_id, :network_id, :station_name, :latitude, :longitude)
                ON CONFLICT (station_id) DO UPDATE
                SET station_name = EXCLUDED.station_name,
                    latitude     = EXCLUDED.latitude,
                    longitude    = EXCLUDED.longitude
            """), {k: r[k] for k in ("station_id", "network_id", "station_name", "latitude", "longitude")})


def _insert_snapshots(records: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO station_snapshots (
                station_id, network_id, station_name, latitude, longitude,
                bikes_available, free_slots, total_capacity,
                utilization_rate, is_critical, status, timestamp
            ) VALUES (
                :station_id, :network_id, :station_name, :latitude, :longitude,
                :bikes_available, :free_slots, :total_capacity,
                :utilization_rate, :is_critical, :status, :timestamp
            )
        """), records)


# ── Pipeline principal ────────────────────────────────────────────────────────

def run_ingestion_once() -> dict[str, Any]:
    ts = datetime.now(timezone.utc).isoformat()
    networks = get_france_networks()
    all_records: list[dict] = []
    processed = 0

    for meta in networks:
        nid = meta["id"]
        time.sleep(1)  # 1s entre chaque réseau pour respecter le rate limit
        try:
            data = fetch_network_details(nid)
            network = data.get("network", {})
            stations = network.get("stations", [])

            # Zone RAW — réponse API brute
            upload_json(BUCKETS["raw"], build_key(f"{nid}.json"), data)

            # Normalisation
            records = [_normalize_station(network, s, ts) for s in stations]
            all_records.extend(records)

            # Zone STAGING — CSV normalisé
            csv_bytes = pd.DataFrame(records).to_csv(index=False).encode("utf-8")
            upload_bytes(BUCKETS["staging"], build_key(f"{nid}.csv"), csv_bytes, "text/csv")

            # PostgreSQL
            _upsert_network(network)
            _upsert_stations(records)
            _insert_snapshots(records)

            processed += 1
            logger.info("[%s] %d stations ingérées", nid, len(records))

        except Exception:
            logger.exception("Erreur réseau %s", nid)

    # Zone CURATED — KPI agrégés par réseau
    if all_records:
        curated = _compute_curated(all_records, ts)
        upload_json(BUCKETS["curated"], build_key("network_kpis.json"), curated)

    return {
        "status":             "ok",
        "timestamp":          ts,
        "networks_processed": processed,
        "records_inserted":   len(all_records),
    }
