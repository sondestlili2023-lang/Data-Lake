"""Évaluation des KPI Vélib Paris — seuils orientés directeur opérations."""
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.analytics import get_executive_summary
from app.alert_snooze import ALERT_HINT
from app.db import engine

THRESHOLDS = {
    "service_score_min_pct": 90.0,
    "stations_to_treat_max": 15,
    "out_of_service_pct_max": 5.0,
    "critical_stations_max": 10,
    "ebike_avg_empty_zones_max": 1.0,
}


def evaluate_kpi_health() -> dict[str, Any]:
    alerts: list[dict[str, Any]] = []
    summary = get_executive_summary()

    if summary["total_stations"] > 0:
        if summary["service_score_pct"] < THRESHOLDS["service_score_min_pct"]:
            alerts.append({
                "kpi": "score_service",
                "severity": "critical",
                "value": summary["service_score_pct"],
                "threshold": THRESHOLDS["service_score_min_pct"],
                "message": (
                    f"Score de service {summary['service_score_pct']}% "
                    f"(seuil : {THRESHOLDS['service_score_min_pct']}%)"
                ),
            })

        if summary["stations_to_treat"] > THRESHOLDS["stations_to_treat_max"]:
            alerts.append({
                "kpi": "stations_a_traiter",
                "severity": "critical",
                "value": summary["stations_to_treat"],
                "threshold": THRESHOLDS["stations_to_treat_max"],
                "message": (
                    f"{summary['stations_to_treat']} stations à traiter "
                    f"({summary['stations_empty']} vides, {summary['stations_full']} pleines, "
                    f"{summary['stations_closed']} hors service)"
                ),
            })

        oos_pct = round(
            summary["stations_closed"] / summary["total_stations"] * 100, 1
        )
        if oos_pct > THRESHOLDS["out_of_service_pct_max"]:
            alerts.append({
                "kpi": "hors_service",
                "severity": "warning",
                "value": oos_pct,
                "threshold": THRESHOLDS["out_of_service_pct_max"],
                "message": (
                    f"{oos_pct}% des stations hors service "
                    f"({summary['stations_closed']} stations)"
                ),
            })

    with engine.connect() as conn:
        critical_count = conn.execute(text("""
            SELECT COUNT(DISTINCT station_id)
            FROM station_snapshots
            WHERE is_critical = TRUE
              AND ingested_at >= NOW() - INTERVAL '10 minutes'
        """)).scalar() or 0

        if critical_count > THRESHOLDS["critical_stations_max"]:
            alerts.append({
                "kpi": "stations_critiques",
                "severity": "critical",
                "value": int(critical_count),
                "threshold": THRESHOLDS["critical_stations_max"],
                "message": (
                    f"{critical_count} stations critiques "
                    f"(seuil : {THRESHOLDS['critical_stations_max']})"
                ),
            })

        ebike_row = conn.execute(text("""
            SELECT ROUND(AVG(bikes_ebike)::NUMERIC, 2) AS avg_ebike
            FROM (
                SELECT DISTINCT ON (station_id)
                    station_id, bikes_ebike, status
                FROM station_snapshots
                WHERE ingested_at >= NOW() - INTERVAL '30 minutes'
                  AND status = 'empty'
                ORDER BY station_id, ingested_at DESC
            ) sub
        """)).fetchone()

        if ebike_row and ebike_row.avg_ebike is not None:
            avg_ebike = float(ebike_row.avg_ebike)
            if avg_ebike < THRESHOLDS["ebike_avg_empty_zones_max"]:
                alerts.append({
                    "kpi": "vae_stations_vides",
                    "severity": "warning",
                    "value": avg_ebike,
                    "threshold": THRESHOLDS["ebike_avg_empty_zones_max"],
                    "message": (
                        f"VAE moyennes sur stations vides : {avg_ebike} "
                        f"(seuil : {THRESHOLDS['ebike_avg_empty_zones_max']})"
                    ),
                })

    return {
        "healthy": len(alerts) == 0,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "thresholds": THRESHOLDS,
        "alert_count": len(alerts),
        "alerts": alerts,
        "executive_summary": summary,
    }


def format_alert_message(status: dict[str, Any]) -> str:
    if status.get("healthy"):
        return ""
    summary = status.get("executive_summary") or {}
    lines = [
        "<b>🚨 Vélib Paris — alerte opérations</b>",
        (
            f"Score service : {summary.get('service_score_pct', '—')}% · "
            f"{summary.get('stations_to_treat', '—')} stations à traiter"
        ),
        "",
    ]
    for a in status.get("alerts", []):
        icon = "🔴" if a["severity"] == "critical" else "🟠"
        lines.append(f"{icon} {a['message']}")
    lines.append("")
    lines.append(ALERT_HINT)
    return "\n".join(lines)


def format_dg_plain_message(status: dict[str, Any]) -> str:
    """Message court pour WhatsApp (sans HTML)."""
    if status.get("healthy"):
        return ""
    summary = status.get("executive_summary") or {}
    parts = [
        f"Vélib Paris : {summary.get('stations_to_treat', 0)} stations à traiter,",
        f"score service {summary.get('service_score_pct', 0)}%.",
    ]
    for a in status.get("alerts", [])[:3]:
        parts.append(a["message"])
    parts.append(ALERT_HINT)
    return " ".join(parts)
