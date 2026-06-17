import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.analytics import (
    get_critical_stations,
    get_geographic_balance,
    get_hourly_criticality,
    get_hourly_peak,
    get_city_efficiency,
    get_ghost_stations,
    get_insufficient_supply_zones,
    get_network_overview,
    get_rebalancing_stations,
    get_station_reliability,
    get_top_stations,
)
from app.ingest import run_ingestion_once
from app.telegram_bot import (
    build_critical_alert_message,
    send_critical_alert,
    send_hourly_summary,
    send_saturation_alert,
    send_message,
    start_polling,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


INGEST_INTERVAL  = 900   # 15 min — respecte le rate limit CityBikes
ALERT_INTERVAL   = 300
HOURLY_INTERVAL  = 3600


def _scheduler_loop() -> None:
    last_ingest      = 0.0
    last_alert_check = 0.0
    last_hourly      = 0.0

    while True:
        now = time.time()

        if now - last_ingest >= INGEST_INTERVAL:
            try:
                result = run_ingestion_once()
                logger.info("Ingestion OK — %d enregistrements", result.get("records_inserted", 0))
            except Exception:
                logger.exception("Erreur d'ingestion")
            last_ingest = now

        if now - last_alert_check >= ALERT_INTERVAL:
            try:
                send_critical_alert()
                send_saturation_alert()
            except Exception:
                logger.exception("Erreur envoi alerte")
            last_alert_check = now

        if datetime.now().minute == 0 and now - last_hourly >= HOURLY_INTERVAL:
            try:
                send_hourly_summary()
            except Exception:
                logger.exception("Erreur résumé horaire")
            last_hourly = now

        time.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=_scheduler_loop, daemon=True, name="scheduler").start()
    start_polling()
    yield


_STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Vélib Data Lakehouse API", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(_STATIC / "dashboard.html")


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok"}


# ── Ingestion manuelle ────────────────────────────────────────────────────────

@app.post("/ingest")
def ingest() -> dict[str, Any]:
    return run_ingestion_once()


# ── Analytics ─────────────────────────────────────────────────────────────────

@app.get("/analytics/top-stations")
def top_stations(limit: int = Query(default=10, ge=1, le=50)):
    return {"items": get_top_stations(limit)}


@app.get("/analytics/critical-stations")
def critical_stations():
    return {"items": get_critical_stations()}


@app.get("/analytics/hourly-peak")
def hourly_peak():
    return {"items": get_hourly_peak()}


@app.get("/analytics/insufficient-supply")
def insufficient_supply():
    return {"items": get_insufficient_supply_zones()}


@app.get("/analytics/geographic-balance")
def geographic_balance():
    return {"items": get_geographic_balance()}


@app.get("/analytics/rebalancing")
def rebalancing(limit: int = Query(default=20, ge=1, le=100)):
    return {"items": get_rebalancing_stations(limit)}


@app.get("/analytics/network-overview")
def network_overview():
    return {"items": get_network_overview()}


# ── KPI Direction ─────────────────────────────────────────────────────────────

@app.get("/analytics/station-reliability")
def station_reliability():
    return {"items": get_station_reliability()}


@app.get("/analytics/ghost-stations")
def ghost_stations():
    return {"items": get_ghost_stations()}


@app.get("/analytics/city-efficiency")
def city_efficiency():
    return {"items": get_city_efficiency()}


@app.get("/analytics/hourly-criticality")
def hourly_criticality():
    return {"items": get_hourly_criticality()}


# ── Telegram ──────────────────────────────────────────────────────────────────

@app.post("/telegram/alert")
def telegram_alert(station_name: str, bikes_available: int, free_slots: int):
    msg  = build_critical_alert_message(station_name, bikes_available, free_slots)
    sent = send_message(msg)
    return {"sent": sent, "message": msg}


@app.post("/telegram/critical-check")
def telegram_critical_check():
    """Déclenche manuellement la vérification d'alertes critiques."""
    send_critical_alert()
    return {"triggered": True}


@app.post("/telegram/hourly-summary")
def telegram_hourly():
    """Déclenche manuellement le résumé horaire."""
    send_hourly_summary()
    return {"triggered": True}
