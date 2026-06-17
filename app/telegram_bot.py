"""
Bot Telegram interactif pour la plateforme Vélib.

Commandes utilisateur :
  /start /help          — aide
  /kpi                  — vue d'ensemble des réseaux
  /critique             — stations critiques (état courant)
  /taux                 — top 15 stations par taux d'utilisation
  /pics                 — pics d'utilisation par heure
  /reequilibrage        — stations à rééquilibrer

Alertes automatiques (déclenchées par le scheduler) :
  • check toutes les 5 min → alerte si > 5 stations critiques par ville
  • résumé horaire à heure pile
"""
import logging
import threading
import time
from datetime import datetime
from typing import Callable

import requests
from sqlalchemy import text

from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from app.db import engine

logger = logging.getLogger(__name__)

_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


# ── Envoi ─────────────────────────────────────────────────────────────────────

def send_message(text_content: str, chat_id: str | None = None) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        return False
    target = chat_id or TELEGRAM_CHAT_ID
    if not target:
        return False
    try:
        r = requests.post(
            f"{_BASE}/sendMessage",
            json={"chat_id": target, "text": text_content, "parse_mode": "HTML"},
            timeout=15,
        )
        return r.ok
    except Exception as e:
        logger.error("Telegram send error: %s", e)
        return False


def build_critical_alert_message(station_name: str, bikes_available: int, free_slots: int) -> str:
    return (
        f"⚠️ Station critique : <b>{station_name}</b>\n"
        f"Vélos disponibles : {bikes_available}\n"
        f"Places libres : {free_slots}"
    )


# ── Long-polling ──────────────────────────────────────────────────────────────

def _get_updates(offset: int) -> list[dict]:
    try:
        r = requests.get(
            f"{_BASE}/getUpdates",
            params={"offset": offset, "timeout": 25},
            timeout=30,
        )
        if r.ok:
            return r.json().get("result", [])
    except Exception as e:
        logger.error("getUpdates error: %s", e)
    return []


# ── Handlers de commandes ─────────────────────────────────────────────────────

def _cmd_start(chat_id: str) -> None:
    send_message(
        "<b>🚲 Bot Vélib Analytics</b>\n\n"
        "Commandes disponibles :\n"
        "/kpi — Vue d'ensemble des réseaux\n"
        "/critique — Stations critiques en ce moment\n"
        "/taux — Top 15 taux d'utilisation (1h)\n"
        "/pics — Pics d'utilisation par heure\n"
        "/reequilibrage — Stations à rééquilibrer\n"
        "/help — Afficher cette aide",
        chat_id,
    )


_LAST = "(SELECT MAX(ingested_at) FROM station_snapshots)"


def _cmd_kpi(chat_id: str) -> None:
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT n.network_name, n.city,
                       COUNT(DISTINCT ss.station_id)               AS nb_stations,
                       SUM(ss.bikes_available)                     AS total_bikes,
                       SUM(ss.total_capacity)                      AS total_capacity,
                       ROUND(AVG(ss.utilization_rate)::NUMERIC, 1) AS avg_utilization,
                       SUM(CASE WHEN ss.is_critical THEN 1 ELSE 0 END) AS nb_critiques,
                       MAX(ss.ingested_at)                         AS last_update
                FROM station_snapshots ss
                JOIN networks n ON n.network_id = ss.network_id
                WHERE ss.ingested_at >= {_LAST} - INTERVAL '30 minutes'
                GROUP BY n.network_id, n.network_name, n.city
                ORDER BY n.city
                LIMIT 20
            """)).fetchall()
        if not rows:
            send_message("Aucune donnée disponible. Vérifiez que l'ingestion tourne.", chat_id)
            return
        lines = ["<b>📊 Vue d'ensemble des réseaux</b>\n"]
        for r in rows:
            lines.append(
                f"🏙️ <b>{r.city}</b> — {r.network_name}\n"
                f"  Vélos dispo : {r.total_bikes} / {r.total_capacity}\n"
                f"  Utilisation moy. : {r.avg_utilization}%\n"
                f"  Stations critiques : {r.nb_critiques} / {r.nb_stations}\n"
                f"  Dernière MàJ : {r.last_update.strftime('%H:%M') if r.last_update else '—'}\n"
            )
        send_message("\n".join(lines), chat_id)
    except Exception as e:
        logger.exception("/kpi error")
        send_message(f"Erreur : {e}", chat_id)


def _cmd_critique(chat_id: str) -> None:
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT DISTINCT ON (station_id)
                    station_name, city, bikes_available, free_slots, status, ingested_at
                FROM analytics_station_snapshot
                WHERE is_critical = TRUE
                  AND ingested_at >= {_LAST} - INTERVAL '30 minutes'
                ORDER BY station_id, ingested_at DESC
                LIMIT 20
            """)).fetchall()
        if not rows:
            send_message("✅ Aucune station critique en ce moment !", chat_id)
            return
        lines = [f"<b>⚠️ Stations critiques ({len(rows)})</b>\n"]
        for r in rows:
            icon  = "🔴" if r.status == "empty" else "🟠"
            label = "VIDE — 0 vélo disponible" if r.status == "empty" else "PLEINE — 0 place libre"
            lines.append(f"{icon} <b>{r.station_name}</b> ({r.city})\n   {label}")
        send_message("\n".join(lines), chat_id)
    except Exception as e:
        logger.exception("/critique error")
        send_message(f"Erreur : {e}", chat_id)


def _cmd_taux(chat_id: str) -> None:
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT station_name, city,
                       ROUND(AVG(utilization_rate)::NUMERIC, 1) AS taux
                FROM analytics_station_snapshot
                WHERE ingested_at >= {_LAST} - INTERVAL '30 minutes'
                  AND total_capacity > 0
                GROUP BY station_id, station_name, city
                ORDER BY taux DESC
                LIMIT 15
            """)).fetchall()
        if not rows:
            send_message("Aucune donnée disponible pour le moment.", chat_id)
            return
        lines = ["<b>📈 Top 15 — Taux d'utilisation (1h)</b>\n<pre>"]
        for i, r in enumerate(rows, 1):
            taux = float(r.taux or 0)
            bar  = "█" * int(taux / 10) + "░" * (10 - int(taux / 10))
            lines.append(f"{i:2}. {r.station_name[:26]:26} ({r.city})\n    {bar} {taux:5.1f}%")
        lines.append("</pre>")
        send_message("\n".join(lines), chat_id)
    except Exception as e:
        logger.exception("/taux error")
        send_message(f"Erreur : {e}", chat_id)


def _cmd_pics(chat_id: str) -> None:
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT EXTRACT(HOUR FROM ingested_at)           AS heure,
                       ROUND(AVG(utilization_rate)::NUMERIC, 1) AS taux
                FROM station_snapshots
                WHERE ingested_at >= NOW() - INTERVAL '7 days'
                GROUP BY EXTRACT(HOUR FROM ingested_at)
                ORDER BY heure
            """)).fetchall()
        if not rows:
            send_message("Pas encore assez de données historiques (1 semaine requise).", chat_id)
            return
        lines = ["<b>⏰ Utilisation par heure de la journée</b>\n<pre>"]
        for r in rows:
            taux = float(r.taux or 0)
            bar  = "█" * int(taux / 10) + "░" * (10 - int(taux / 10))
            lines.append(f"{int(r.heure):02d}h │{bar}│ {taux:5.1f}%")
        lines.append("</pre>")
        send_message("\n".join(lines), chat_id)
    except Exception as e:
        logger.exception("/pics error")
        send_message(f"Erreur : {e}", chat_id)


def _cmd_reequilibrage(chat_id: str) -> None:
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT station_name, city,
                       ROUND(
                           COUNT(*) FILTER (WHERE is_critical)::NUMERIC
                           / NULLIF(COUNT(*), 0) * 100, 1
                       )                                          AS pct_critique,
                       ROUND(AVG(bikes_available)::NUMERIC, 1)   AS velos_moy,
                       COUNT(*) FILTER (WHERE status = 'empty')  AS nb_vides,
                       COUNT(*) FILTER (WHERE status = 'full')   AS nb_pleines
                FROM analytics_station_snapshot
                WHERE ingested_at >= {_LAST} - INTERVAL '30 minutes'
                GROUP BY station_id, station_name, city
                HAVING COUNT(*) FILTER (WHERE is_critical) > 0
                ORDER BY pct_critique DESC
                LIMIT 15
            """)).fetchall()
        if not rows:
            send_message("✅ Aucune station ne nécessite de rééquilibrage.", chat_id)
            return
        lines = [f"<b>🔄 Stations à rééquilibrer ({len(rows)})</b>\n"]
        for r in rows:
            lines.append(
                f"📍 <b>{r.station_name[:30]}</b> ({r.city})\n"
                f"   Criticité : {r.pct_critique}% — Vélos moy. : {r.velos_moy}\n"
                f"   🔴 {r.nb_vides} vides | 🟠 {r.nb_pleines} pleines"
            )
        send_message("\n".join(lines), chat_id)
    except Exception as e:
        logger.exception("/reequilibrage error")
        send_message(f"Erreur : {e}", chat_id)


_COMMANDS: dict[str, Callable[[str], None]] = {
    "/start":          _cmd_start,
    "/help":           _cmd_start,
    "/kpi":            _cmd_kpi,
    "/critique":       _cmd_critique,
    "/taux":           _cmd_taux,
    "/pics":           _cmd_pics,
    "/reequilibrage":  _cmd_reequilibrage,
}


def _handle_update(update: dict) -> None:
    msg = update.get("message", {})
    text_content = msg.get("text", "")
    chat_id = str(msg.get("chat", {}).get("id", ""))
    if not chat_id or not text_content.startswith("/"):
        return
    command = text_content.split()[0].split("@")[0].lower()
    handler = _COMMANDS.get(command)
    if handler:
        try:
            handler(chat_id)
        except Exception:
            logger.exception("Handler error for %s", command)


def _polling_loop() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN non défini — polling désactivé")
        return
    offset = 0
    logger.info("Bot Telegram démarré (long-polling)")
    while True:
        try:
            for update in _get_updates(offset):
                offset = update["update_id"] + 1
                _handle_update(update)
        except Exception as e:
            logger.error("Polling loop error: %s", e)
            time.sleep(5)


def start_polling() -> None:
    t = threading.Thread(target=_polling_loop, daemon=True, name="telegram-polling")
    t.start()


# ── Alertes automatiques ──────────────────────────────────────────────────────

def send_critical_alert() -> None:
    """
    Automatisation 1 — Alerte si une ville dépasse 5 stations critiques.
    Condition : nb_critiques > 5 dans la dernière fenêtre d'ingestion (10 min).
    Action    : message Telegram avec détail par ville.
    """
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT city,
                       COUNT(*)                                      AS nb_critiques,
                       COUNT(*) FILTER (WHERE status = 'empty')     AS nb_vides,
                       COUNT(*) FILTER (WHERE status = 'full')      AS nb_pleines
                FROM (
                    SELECT DISTINCT ON (station_id)
                        n.city, ss.status, ss.ingested_at
                    FROM station_snapshots ss
                    JOIN networks n ON n.network_id = ss.network_id
                    WHERE ss.is_critical = TRUE
                      AND ss.ingested_at >= NOW() - INTERVAL '10 minutes'
                    ORDER BY station_id, ss.ingested_at DESC
                ) sub
                GROUP BY city
                HAVING COUNT(*) > 5
                ORDER BY nb_critiques DESC
            """)).fetchall()
        if not rows:
            return
        lines = ["<b>🚨 ALERTE — Stations critiques</b>\n"]
        for r in rows:
            lines.append(
                f"🏙️ <b>{r.city}</b> : {r.nb_critiques} stations critiques\n"
                f"   🔴 Vides : {r.nb_vides} | 🟠 Pleines : {r.nb_pleines}"
            )
        send_message("\n".join(lines))
    except Exception:
        logger.exception("send_critical_alert error")


def send_saturation_alert() -> None:
    """
    Automatisation 2 — Alerte de saturation réseau.
    Condition : taux d'utilisation moyen d'une ville >= 85% sur la dernière heure.
    Action    : message Telegram signalant le réseau saturé + top 3 stations à vider.
    """
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    try:
        with engine.connect() as conn:
            # Villes dont le taux moyen dépasse 85 %
            villes = conn.execute(text("""
                SELECT n.city,
                       COUNT(DISTINCT ss.station_id)               AS nb_stations,
                       ROUND(AVG(ss.utilization_rate)::NUMERIC, 1) AS taux_moyen,
                       SUM(ss.bikes_available)                     AS total_velos,
                       SUM(ss.total_capacity)                      AS total_cap
                FROM station_snapshots ss
                JOIN networks n ON n.network_id = ss.network_id
                WHERE ss.ingested_at >= NOW() - INTERVAL '1 hour'
                GROUP BY n.city
                HAVING AVG(ss.utilization_rate) >= 85
                ORDER BY taux_moyen DESC
                LIMIT 5
            """)).fetchall()

            if not villes:
                return

            lines = ["<b>🔥 ALERTE SATURATION — Réseau sous tension</b>\n"]
            for v in villes:
                # Top 3 stations les plus critiques de cette ville
                top = conn.execute(text("""
                    SELECT ss.station_name,
                           ss.bikes_available,
                           ss.free_slots,
                           ROUND(AVG(ss.utilization_rate)::NUMERIC, 0) AS taux
                    FROM station_snapshots ss
                    JOIN networks n ON n.network_id = ss.network_id
                    WHERE n.city = :city
                      AND ss.ingested_at >= NOW() - INTERVAL '1 hour'
                    GROUP BY ss.station_id, ss.station_name, ss.bikes_available, ss.free_slots
                    ORDER BY taux DESC
                    LIMIT 3
                """), {"city": v.city}).fetchall()

                lines.append(
                    f"🏙️ <b>{v.city}</b> — taux moyen : <b>{v.taux_moyen}%</b>\n"
                    f"   {v.nb_stations} stations | {v.total_velos}/{v.total_cap} vélos\n"
                    f"   Top stations saturées :"
                )
                for s in top:
                    lines.append(
                        f"     • {s.station_name[:30]} → {s.taux}%"
                        f" ({s.bikes_available} vélos / {s.free_slots} places)"
                    )
                lines.append("")

            lines.append("⚡ Rééquilibrage recommandé — consultez /reequilibrage")
            send_message("\n".join(lines))
    except Exception:
        logger.exception("send_saturation_alert error")


def send_hourly_summary() -> None:
    """
    Automatisation 3 — Rapport horaire automatique.
    Condition : déclenchement à heure pile (scheduler).
    Action    : bilan national + top 5 réseaux les plus chargés.
    """
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT n.city, n.network_name,
                       COUNT(DISTINCT ss.station_id)               AS nb_stations,
                       SUM(ss.bikes_available)                     AS total_bikes,
                       SUM(ss.total_capacity)                      AS total_capacity,
                       ROUND(AVG(ss.utilization_rate)::NUMERIC, 1) AS avg_utilization,
                       SUM(CASE WHEN ss.is_critical THEN 1 ELSE 0 END) AS nb_critiques
                FROM station_snapshots ss
                JOIN networks n ON n.network_id = ss.network_id
                WHERE ss.ingested_at >= NOW() - INTERVAL '1 hour'
                GROUP BY n.network_id, n.city, n.network_name
                ORDER BY avg_utilization DESC
            """)).fetchall()
        if not rows:
            return
        now         = datetime.now().strftime("%H:%M")
        total_bikes = sum(r.total_bikes or 0 for r in rows)
        total_cap   = sum(r.total_capacity or 0 for r in rows)
        total_crit  = sum(r.nb_critiques or 0 for r in rows)
        pct         = round(total_bikes / total_cap * 100, 1) if total_cap else 0

        lines = [
            f"<b>📊 Rapport horaire — {now}</b>\n",
            f"🇫🇷 <b>Bilan national ({len(rows)} réseaux)</b>",
            f"   Vélos disponibles : {total_bikes:,} / {total_cap:,} ({pct}%)",
            f"   Stations critiques : {total_crit}\n",
            "<b>Top 5 réseaux les plus chargés :</b>",
        ]
        for r in list(rows)[:5]:
            bar = "█" * int(float(r.avg_utilization or 0) / 10) + "░" * (10 - int(float(r.avg_utilization or 0) / 10))
            lines.append(
                f"• <b>{r.city}</b> : {bar} {r.avg_utilization}%"
                f" — ⚠️ {r.nb_critiques} critiques"
            )
        send_message("\n".join(lines))
    except Exception:
        logger.exception("send_hourly_summary error")
