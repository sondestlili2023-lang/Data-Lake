import logging
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests
from sqlalchemy import text

from app.config import (
    CITYBIKES_API_URL,
    ENABLE_JCDECAUX,
    INGEST_SCOPE,
    JCDECAUX_API_KEY,
    JCDECAUX_API_URL,
    JCDECAUX_CONTRACTS,
    VELIB_GBFS_BASE,
)
from app.db import engine
from app.minio_client import BUCKETS, build_key, upload_bytes, upload_json

logger = logging.getLogger(__name__)

_NETWORKS_CACHE: list[dict] = []
_NETWORKS_CACHE_TS: float = 0.0
_NETWORKS_CACHE_TTL = 600  # 10 min


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _fetch(url: str, retries: int = 3) -> Any:
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


# ── CityBikes ─────────────────────────────────────────────────────────────────

def get_france_networks() -> list[dict[str, Any]]:
    global _NETWORKS_CACHE, _NETWORKS_CACHE_TS
    if time.time() - _NETWORKS_CACHE_TS < _NETWORKS_CACHE_TTL and _NETWORKS_CACHE:
        return _NETWORKS_CACHE
    data = _fetch(f"{CITYBIKES_API_URL}/networks")
    _NETWORKS_CACHE = [n for n in data.get("networks", []) if n.get("location", {}).get("country") == "FR"]
    _NETWORKS_CACHE_TS = time.time()
    logger.info("Cache réseaux CityBikes mis à jour : %d réseaux FR", len(_NETWORKS_CACHE))
    return _NETWORKS_CACHE


def fetch_network_details(network_id: str) -> dict[str, Any]:
    return _fetch(f"{CITYBIKES_API_URL}/networks/{network_id}")


# ── Vélib Paris (GBFS officiel) ───────────────────────────────────────────────

def fetch_velib_gbfs() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    info = _fetch(f"{VELIB_GBFS_BASE}/station_information.json")
    status = _fetch(f"{VELIB_GBFS_BASE}/station_status.json")
    info_by_id = {s["station_id"]: s for s in info.get("data", {}).get("stations", [])}
    merged: list[dict[str, Any]] = []
    for st in status.get("data", {}).get("stations", []):
        meta = info_by_id.get(st.get("station_id"), {})
        merged.append({**meta, **st})
    return {"information": info, "status": status}, merged


# ── JCDecaux ──────────────────────────────────────────────────────────────────

def get_jcdecaux_contracts() -> list[dict[str, Any]]:
    if not JCDECAUX_API_KEY:
        return []
    if JCDECAUX_CONTRACTS.strip():
        names = [c.strip() for c in JCDECAUX_CONTRACTS.split(",") if c.strip()]
        return [{"name": n, "commercial_name": n, "cities": [n.title()], "country_code": "FR"} for n in names]
    data = _fetch(f"{JCDECAUX_API_URL}/contracts?apiKey={JCDECAUX_API_KEY}")
    if not isinstance(data, list):
        return []
    contracts = [c for c in data if c.get("country_code") == "FR"]
    logger.info("Contrats JCDecaux FR : %d", len(contracts))
    return contracts


def fetch_jcdecaux_stations(contract_name: str) -> list[dict[str, Any]]:
    url = f"{JCDECAUX_API_URL}/stations?contract={contract_name}&apiKey={JCDECAUX_API_KEY}"
    data = _fetch(url)
    return data if isinstance(data, list) else []


# ── Normalisation ─────────────────────────────────────────────────────────────

def _parse_bike_types(types: list) -> tuple[int, int]:
    mech = ebike = 0
    for entry in types or []:
        if not isinstance(entry, dict):
            continue
        if "mechanical" in entry:
            mech = int(entry["mechanical"] or 0)
        if "ebike" in entry:
            ebike = int(entry["ebike"] or 0)
    return mech, ebike


def _epoch_to_ts(epoch: Any) -> datetime | None:
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def _snapshot_extras(
    *,
    bikes_mechanical: int = 0,
    bikes_ebike: int = 0,
    is_installed: bool = True,
    is_renting: bool = True,
    is_returning: bool = True,
    last_reported: datetime | None = None,
) -> dict[str, Any]:
    return {
        "bikes_mechanical": bikes_mechanical,
        "bikes_ebike": bikes_ebike,
        "is_installed": is_installed,
        "is_renting": is_renting,
        "is_returning": is_returning,
        "last_reported": last_reported,
    }


def _normalize_citybikes_station(network: dict, station: dict, ts: str) -> dict[str, Any]:
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
        "data_source":      "citybikes",
        **_snapshot_extras(bikes_mechanical=bikes),
    }


def _normalize_velib_gbfs_station(station: dict, ts: str) -> dict[str, Any]:
    bikes = int(station.get("num_bikes_available") or station.get("numBikesAvailable") or 0)
    slots = int(station.get("num_docks_available") or station.get("numDocksAvailable") or 0)
    capacity = int(station.get("capacity") or 0)
    if capacity <= 0:
        capacity = bikes + slots

    if capacity > 0:
        utilization_rate = round((1 - bikes / capacity) * 100, 2)
    else:
        utilization_rate = 0.0

    if not station.get("is_renting", 1):
        status = "closed"
    elif bikes == 0:
        status = "empty"
    elif slots == 0:
        status = "full"
    else:
        status = "ok"

    mech, ebike = _parse_bike_types(station.get("num_bikes_available_types"))
    if mech == 0 and ebike == 0 and bikes > 0:
        mech = bikes

    code = station.get("stationCode") or station.get("station_id")
    return {
        "station_id":       f"velib-{code}",
        "network_id":       "velib-paris",
        "network_name":     "Vélib' Métropole",
        "station_name":     station.get("name", f"Station {code}"),
        "latitude":         station.get("lat"),
        "longitude":        station.get("lon"),
        "city":             "Paris",
        "country":          "FR",
        "bikes_available":  bikes,
        "free_slots":       slots,
        "total_capacity":   capacity,
        "utilization_rate": utilization_rate,
        "is_critical":      status in ("empty", "full"),
        "status":           status,
        "timestamp":        ts,
        "data_source":      "velib_gbfs",
        **_snapshot_extras(
            bikes_mechanical=mech,
            bikes_ebike=ebike,
            is_installed=bool(station.get("is_installed", 1)),
            is_renting=bool(station.get("is_renting", 1)),
            is_returning=bool(station.get("is_returning", 1)),
            last_reported=_epoch_to_ts(station.get("last_reported")),
        ),
    }


def _normalize_jcdecaux_station(contract: dict, station: dict, ts: str) -> dict[str, Any] | None:
    contract_name = station.get("contract_name") or contract.get("name", "")
    network_id = f"jcdecaux-{contract_name}"
    station_number = station.get("number")
    if station_number is None:
        return None
    station_id = f"{network_id}-{station_number}"

    bikes = int(station.get("available_bikes") or 0)
    slots = int(station.get("available_bike_stands") or 0)
    capacity = int(station.get("bike_stands") or 0)
    if capacity <= 0:
        capacity = bikes + slots

    if capacity > 0:
        utilization_rate = round((1 - bikes / capacity) * 100, 2)
    else:
        utilization_rate = 0.0

    if station.get("status") != "OPEN":
        status = "closed"
    elif bikes == 0:
        status = "empty"
    elif slots == 0:
        status = "full"
    else:
        status = "ok"

    pos = station.get("position") or {}
    cities = contract.get("cities") or []
    city = ", ".join(cities) if cities else contract_name.title()

    return {
        "station_id":       station_id,
        "network_id":       network_id,
        "network_name":     f"JCDecaux — {contract.get('commercial_name', contract_name)}",
        "station_name":     station.get("name", ""),
        "latitude":         pos.get("lat"),
        "longitude":        pos.get("lng"),
        "city":             city,
        "country":          contract.get("country_code", "FR"),
        "bikes_available":  bikes,
        "free_slots":       slots,
        "total_capacity":   capacity,
        "utilization_rate": utilization_rate,
        "is_critical":      status in ("empty", "full"),
        "status":           status,
        "timestamp":        ts,
        "data_source":      "jcdecaux",
        **_snapshot_extras(bikes_mechanical=bikes),
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
                "data_source":   r.get("data_source", ""),
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
    snapshot_keys = (
        "station_id", "network_id", "station_name", "latitude", "longitude",
        "bikes_available", "free_slots", "total_capacity",
        "utilization_rate", "is_critical", "status", "timestamp",
        "bikes_mechanical", "bikes_ebike",
        "is_installed", "is_renting", "is_returning", "last_reported",
    )
    with engine.begin() as conn:
        for r in records:
            row = {k: r.get(k) for k in snapshot_keys}
            conn.execute(text("""
                INSERT INTO station_snapshots (
                    station_id, network_id, station_name, latitude, longitude,
                    bikes_available, free_slots, total_capacity,
                    utilization_rate, is_critical, status, timestamp,
                    bikes_mechanical, bikes_ebike,
                    is_installed, is_renting, is_returning, last_reported
                ) VALUES (
                    :station_id, :network_id, :station_name, :latitude, :longitude,
                    :bikes_available, :free_slots, :total_capacity,
                    :utilization_rate, :is_critical, :status, :timestamp,
                    :bikes_mechanical, :bikes_ebike,
                    :is_installed, :is_renting, :is_returning, :last_reported
                )
            """), row)


def _persist_network_batch(network: dict, records: list[dict], raw_key: str, raw_payload: Any) -> None:
    upload_json(BUCKETS["raw"], build_key(raw_key), raw_payload)
    if records:
        csv_bytes = pd.DataFrame(records).to_csv(index=False).encode("utf-8")
        upload_bytes(BUCKETS["staging"], build_key(f"{raw_key}.csv"), csv_bytes, "text/csv")
    _upsert_network(network)
    _upsert_stations(records)
    _insert_snapshots(records)


# ── Pipelines par source ──────────────────────────────────────────────────────

def _ingest_citybikes(ts: str, network_ids: list[str] | None = None) -> tuple[list[dict], int]:
    if network_ids:
        networks = [{"id": nid} for nid in network_ids]
    else:
        networks = get_france_networks()
    all_records: list[dict] = []
    processed = 0

    for meta in networks:
        nid = meta["id"]
        time.sleep(1)
        try:
            data = fetch_network_details(nid)
            network = data.get("network", {})
            stations = network.get("stations", [])
            records = [_normalize_citybikes_station(network, s, ts) for s in stations]
            all_records.extend(records)
            _persist_network_batch(network, records, f"citybikes-{nid}.json", data)
            processed += 1
            logger.info("[citybikes:%s] %d stations", nid, len(records))
        except Exception:
            logger.exception("[citybikes] Erreur réseau %s", nid)

    return all_records, processed


def _ingest_velib_gbfs(ts: str) -> tuple[list[dict], int]:
    raw, stations = fetch_velib_gbfs()
    network = {
        "id": "velib-paris",
        "name": "Vélib' Métropole",
        "location": {"city": "Paris", "country": "FR"},
    }
    records = [_normalize_velib_gbfs_station(s, ts) for s in stations]
    _persist_network_batch(network, records, "velib-gbfs-paris.json", raw)
    logger.info("[velib_gbfs:paris] %d stations", len(records))
    return records, 1


def _ingest_paris(ts: str) -> dict[str, Any]:
    gbfs_records: list[dict] = []
    gbfs_ok = 0
    try:
        gbfs_records, gbfs_ok = _ingest_velib_gbfs(ts)
    except Exception:
        logger.exception("[velib_gbfs] Flux officiel indisponible")

    cb_records, cb_ok = _ingest_citybikes(ts, network_ids=["velib"])
    all_records = gbfs_records if gbfs_records else cb_records

    if all_records:
        curated = _compute_curated(all_records, ts)
        upload_json(BUCKETS["curated"], build_key("network_kpis.json"), curated)

    primary = "velib_gbfs" if gbfs_records else ("citybikes" if cb_records else "none")
    return {
        "status": "ok",
        "scope": "paris",
        "primary_source": primary,
        "velib_gbfs": {"networks_processed": gbfs_ok, "records_inserted": len(gbfs_records)},
        "citybikes": {"networks_processed": cb_ok, "records_inserted": len(cb_records)},
        "networks_processed": gbfs_ok + cb_ok,
        "records_inserted": len(all_records),
        "timestamp": ts,
    }


def _ingest_jcdecaux(ts: str) -> tuple[list[dict], int]:
    if not JCDECAUX_API_KEY:
        logger.info("JCDECAUX_API_KEY absent — ingestion JCDecaux ignorée")
        return [], 0

    contracts = get_jcdecaux_contracts()
    all_records: list[dict] = []
    processed = 0

    for contract in contracts:
        cname = contract["name"]
        time.sleep(0.5)
        try:
            stations = fetch_jcdecaux_stations(cname)
            network = {
                "id": f"jcdecaux-{cname}",
                "name": f"JCDecaux — {contract.get('commercial_name', cname)}",
                "location": {
                    "city": ", ".join(contract.get("cities", [])) or cname.title(),
                    "country": contract.get("country_code", "FR"),
                },
            }
            records = [
                r for s in stations
                if (r := _normalize_jcdecaux_station(contract, s, ts)) is not None
            ]
            all_records.extend(records)
            _persist_network_batch(
                network,
                records,
                f"jcdecaux-{cname}.json",
                {"contract": contract, "stations": stations},
            )
            processed += 1
            logger.info("[jcdecaux:%s] %d stations", cname, len(records))
        except Exception:
            logger.exception("[jcdecaux] Erreur contrat %s", cname)

    return all_records, processed


# ── Pipeline principal ────────────────────────────────────────────────────────

def run_ingestion_once() -> dict[str, Any]:
    ts = datetime.now(timezone.utc).isoformat()

    if INGEST_SCOPE == "paris":
        return _ingest_paris(ts)

    cb_records, cb_networks = _ingest_citybikes(ts)
    jd_records: list[dict] = []
    jd_contracts = 0
    if ENABLE_JCDECAUX:
        jd_records, jd_contracts = _ingest_jcdecaux(ts)
    all_records = cb_records + jd_records

    if all_records:
        curated = _compute_curated(all_records, ts)
        upload_json(BUCKETS["curated"], build_key("network_kpis.json"), curated)

    return {
        "status": "ok",
        "scope": INGEST_SCOPE,
        "timestamp": ts,
        "citybikes": {
            "networks_processed": cb_networks,
            "records_inserted": len(cb_records),
        },
        "jcdecaux": {
            "contracts_processed": jd_contracts,
            "records_inserted": len(jd_records),
        },
        "networks_processed": cb_networks + jd_contracts,
        "records_inserted": len(all_records),
    }
