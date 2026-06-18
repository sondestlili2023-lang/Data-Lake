import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.alert_recipients import (
    add_recipient,
    delete_recipient,
    is_sender_phone,
    list_dispatch_recipients,
    list_recipients,
    sync_openclaw_allowlist,
)
from app.alert_snooze import is_contact_snoozed, list_active_snoozes, snooze_contact
from app.analytics import (
    get_critical_stations,
    get_executive_summary,
    get_fleet_mix,
    get_geographic_balance,
    get_hourly_peak,
    get_insufficient_supply_zones,
    get_network_overview,
    get_priority_stations,
    get_rebalancing_stations,
    get_top_stations,
)
from app.config import ALERTS_ADMIN_TOKEN, OPENCLAW_WHATSAPP_SENDER, TELEGRAM_CHAT_ID
from app.ingest import run_ingestion_once
from app.kpi_health import (
    evaluate_kpi_health,
    format_alert_message,
    format_dg_plain_message,
)
from app.telegram_bot import build_critical_alert_message, send_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

_STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Vélib Data Lakehouse API")
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


class RecipientCreate(BaseModel):
    phone: str
    label: str | None = None


class SnoozeCreate(BaseModel):
    channel: str
    contact: str
    hours: float | None = None


def _require_admin_token(token: str | None) -> None:
    if ALERTS_ADMIN_TOKEN and token != ALERTS_ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Token admin invalide")


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(_STATIC / "dashboard.html")


@app.get("/alerts", include_in_schema=False)
def alerts_page():
    return FileResponse(_STATIC / "alerts.html")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok"}


@app.post("/ingest")
def ingest() -> dict[str, Any]:
    return run_ingestion_once()


@app.get("/analytics/kpi-status")
def kpi_status() -> dict[str, Any]:
    return evaluate_kpi_health()


@app.get("/analytics/executive-summary")
def executive_summary():
    return get_executive_summary()


@app.get("/analytics/fleet-mix")
def fleet_mix():
    return get_fleet_mix()


@app.get("/analytics/priority-stations")
def priority_stations(limit: int = Query(default=15, ge=1, le=50)):
    return {"items": get_priority_stations(limit)}


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


@app.get("/alerts/recipients")
def alerts_recipients_list(for_dispatch: bool = Query(default=False)):
    items = list_dispatch_recipients() if for_dispatch else list_recipients(active_only=True)
    return {
        "items": items,
        "count": len(items),
        "sender_excluded": OPENCLAW_WHATSAPP_SENDER or None,
    }


@app.post("/alerts/recipients")
def alerts_recipients_add(
    body: RecipientCreate,
    x_alerts_token: str | None = Header(None, alias="X-Alerts-Token"),
):
    _require_admin_token(x_alerts_token)
    try:
        return add_recipient(body.phone, body.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/alerts/recipients/{recipient_id}")
def alerts_recipients_delete(
    recipient_id: int,
    x_alerts_token: str | None = Header(None, alias="X-Alerts-Token"),
):
    _require_admin_token(x_alerts_token)
    return delete_recipient(recipient_id)


@app.post("/alerts/sync-openclaw")
def alerts_sync_openclaw(
    x_alerts_token: str | None = Header(None, alias="X-Alerts-Token"),
):
    _require_admin_token(x_alerts_token)
    return sync_openclaw_allowlist()


@app.get("/alerts/snooze-status")
def alerts_snooze_status():
    items = list_active_snoozes()
    return {"items": items, "count": len(items)}


@app.post("/alerts/snooze")
def alerts_snooze(body: SnoozeCreate):
    try:
        return snooze_contact(body.channel, body.contact, body.hours)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/alerts/dispatch")
def alerts_dispatch():
    status = evaluate_kpi_health()
    if status["healthy"]:
        return {"sent": False, "healthy": True, "status": status}

    msg_html = format_alert_message(status)
    msg_plain = format_dg_plain_message(status)

    telegram_snoozed = is_contact_snoozed("telegram", TELEGRAM_CHAT_ID)
    telegram_sent = False
    if TELEGRAM_CHAT_ID and not telegram_snoozed:
        telegram_sent = send_message(msg_html)

    wa_recipients = list_dispatch_recipients()
    wa_snoozed = sum(
        1 for r in list_recipients(active_only=True)
        if not is_sender_phone(r["phone_e164"])
        and is_contact_snoozed("whatsapp", r["phone_e164"])
    )
    any_sent = telegram_sent or len(wa_recipients) > 0

    return {
        "sent": any_sent,
        "healthy": False,
        "status": status,
        "message_html": msg_html,
        "message": msg_plain,
        "telegram_sent": telegram_sent,
        "telegram_snoozed": telegram_snoozed,
        "whatsapp_recipients": [r["phone_e164"] for r in wa_recipients],
        "whatsapp_snoozed_count": wa_snoozed,
        "whatsapp_dispatch_hint": "Exécutez scripts/dispatch_whatsapp_alerts.ps1 sur le host Windows.",
    }


@app.post("/telegram/alert")
def telegram_alert(station_name: str, bikes_available: int, free_slots: int):
    msg = build_critical_alert_message(station_name, bikes_available, free_slots)
    sent = send_message(msg)
    return {"sent": sent, "message": msg}


@app.post("/telegram/kpi-alert-check")
def telegram_kpi_alert_check():
    status = evaluate_kpi_health()
    if status["healthy"]:
        return {"sent": False, "healthy": True, "status": status}
    if is_contact_snoozed("telegram", TELEGRAM_CHAT_ID):
        return {"sent": False, "healthy": False, "snoozed": True, "status": status}
    msg = format_alert_message(status)
    sent = send_message(msg)
    return {"sent": sent, "healthy": False, "status": status, "message": msg}
