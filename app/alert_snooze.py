"""Pause des alertes KPI par contact (Telegram / WhatsApp)."""
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from app.config import ALERT_SNOOZE_HOURS
from app.db import engine

ALERT_HINT = "Répondez OK pour suspendre 6 h. Écrivez vélib pour l'état du réseau."


def build_contact_key(channel: str, contact: str) -> str:
    return f"{channel.lower().strip()}:{contact.strip()}"


def _cleanup_expired(conn) -> None:
    conn.execute(text("""
        DELETE FROM alert_snooze WHERE paused_until <= NOW()
    """))


def is_snoozed(contact_key: str) -> bool:
    with engine.connect() as conn:
        _cleanup_expired(conn)
        row = conn.execute(text("""
            SELECT paused_until FROM alert_snooze
            WHERE contact_key = :key AND paused_until > NOW()
        """), {"key": contact_key}).fetchone()
    return row is not None


def is_contact_snoozed(channel: str, contact: str) -> bool:
    if not contact:
        return False
    return is_snoozed(build_contact_key(channel, contact))


def snooze_contact(
    channel: str,
    contact: str,
    hours: float | None = None,
) -> dict[str, Any]:
    channel = channel.lower().strip()
    if channel not in ("whatsapp", "telegram"):
        raise ValueError("channel doit être whatsapp ou telegram")
    if not contact or not contact.strip():
        raise ValueError("contact requis")

    duration = hours if hours is not None else ALERT_SNOOZE_HOURS
    contact_key = build_contact_key(channel, contact.strip())
    paused_until = datetime.now(timezone.utc) + timedelta(hours=duration)

    with engine.begin() as conn:
        row = conn.execute(text("""
            INSERT INTO alert_snooze (contact_key, channel, paused_until)
            VALUES (:key, :channel, :until)
            ON CONFLICT (contact_key) DO UPDATE
                SET paused_until = EXCLUDED.paused_until,
                    paused_at = NOW()
            RETURNING contact_key, channel, paused_until, paused_at
        """), {
            "key": contact_key,
            "channel": channel,
            "until": paused_until.replace(tzinfo=None),
        }).fetchone()

    return {
        "contact_key": row.contact_key,
        "channel": row.channel,
        "contact": contact.strip(),
        "paused_until": row.paused_until.isoformat() if row.paused_until else None,
        "paused_at": row.paused_at.isoformat() if row.paused_at else None,
        "hours": duration,
        "message": f"Alertes suspendues {duration:g} h. Écrivez vélib pour consulter l'état à tout moment.",
    }


def list_active_snoozes() -> list[dict[str, Any]]:
    with engine.connect() as conn:
        _cleanup_expired(conn)
        rows = conn.execute(text("""
            SELECT contact_key, channel, paused_until, paused_at
            FROM alert_snooze
            WHERE paused_until > NOW()
            ORDER BY paused_until ASC
        """)).fetchall()

    items = []
    for r in rows:
        contact = r.contact_key.split(":", 1)[-1] if r.contact_key else ""
        items.append({
            "contact_key": r.contact_key,
            "channel": r.channel,
            "contact": contact,
            "paused_until": r.paused_until.isoformat() if r.paused_until else None,
            "paused_at": r.paused_at.isoformat() if r.paused_at else None,
        })
    return items
