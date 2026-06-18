"""Gestion des destinataires d'alertes WhatsApp + sync OpenClaw allowlist."""
import json
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text

from app.alert_snooze import is_contact_snoozed
from app.config import OPENCLAW_CONFIG_PATH, OPENCLAW_WHATSAPP_SENDER
from app.db import engine
from app.openclaw_restart import request_openclaw_gateway_restart

logger = logging.getLogger(__name__)

_E164_RE = re.compile(r"^\+[1-9]\d{9,14}$")


def normalize_phone(phone: str) -> str:
    raw = (phone or "").strip()
    if not raw:
        raise ValueError("Numéro requis")
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        normalized = "+" + digits
    elif digits.startswith("33"):
        normalized = "+" + digits
    elif digits.startswith("0") and len(digits) >= 10:
        normalized = "+33" + digits[1:]
    else:
        normalized = "+" + digits
    if not _E164_RE.match(normalized):
        raise ValueError("Format invalide — utilisez E.164 (ex. +33612345678)")
    return normalized


def is_sender_phone(phone: str) -> bool:
    if not OPENCLAW_WHATSAPP_SENDER:
        return False
    try:
        return normalize_phone(phone) == normalize_phone(OPENCLAW_WHATSAPP_SENDER)
    except ValueError:
        return phone.strip() == OPENCLAW_WHATSAPP_SENDER.strip()


def list_recipients(active_only: bool = True) -> list[dict[str, Any]]:
    clause = "WHERE active = TRUE" if active_only else ""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT id, phone_e164, label, channel, active, created_at
            FROM alert_recipients
            {clause}
            ORDER BY created_at DESC
        """)).fetchall()
    return [dict(r._mapping) for r in rows]


def list_dispatch_recipients() -> list[dict[str, Any]]:
    """Destinataires WhatsApp actifs, hors expéditeur OpenClaw et contacts en pause."""
    items = []
    for r in list_recipients(active_only=True):
        phone = r["phone_e164"]
        if is_sender_phone(phone):
            continue
        if is_contact_snoozed("whatsapp", phone):
            continue
        items.append(r)
    return items


def _allowlist_phones() -> list[str]:
    """allowFrom = expéditeur + tous les destinataires actifs (inbound)."""
    phones: list[str] = []
    if OPENCLAW_WHATSAPP_SENDER:
        try:
            phones.append(normalize_phone(OPENCLAW_WHATSAPP_SENDER))
        except ValueError:
            phones.append(OPENCLAW_WHATSAPP_SENDER.strip())
    for r in list_recipients(active_only=True):
        phone = r["phone_e164"]
        if phone not in phones:
            phones.append(phone)
    return phones


def add_recipient(phone: str, label: str | None = None) -> dict[str, Any]:
    phone_e164 = normalize_phone(phone)
    if is_sender_phone(phone_e164):
        raise ValueError(
            f"{phone_e164} est le numéro expéditeur OpenClaw — il ne peut pas être destinataire d'alerte."
        )
    with engine.begin() as conn:
        row = conn.execute(text("""
            INSERT INTO alert_recipients (phone_e164, label)
            VALUES (:phone, :label)
            ON CONFLICT (phone_e164) DO UPDATE
                SET label = COALESCE(EXCLUDED.label, alert_recipients.label),
                    active = TRUE
            RETURNING id, phone_e164, label, channel, active, created_at
        """), {"phone": phone_e164, "label": label}).fetchone()
    sync = sync_openclaw_allowlist()
    result = dict(row._mapping)
    result["openclaw_sync"] = sync
    return result


def delete_recipient(recipient_id: int) -> dict[str, Any]:
    with engine.begin() as conn:
        row = conn.execute(text("""
            DELETE FROM alert_recipients WHERE id = :id
            RETURNING id, phone_e164
        """), {"id": recipient_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Destinataire introuvable")
    sync = sync_openclaw_allowlist()
    return {"deleted": dict(row._mapping), "openclaw_sync": sync}


def sync_openclaw_allowlist() -> dict[str, Any]:
    if not OPENCLAW_CONFIG_PATH:
        return {"synced": False, "reason": "OPENCLAW_CONFIG_PATH non configuré"}

    path = Path(OPENCLAW_CONFIG_PATH)
    if not path.exists():
        return {"synced": False, "reason": f"Fichier introuvable : {path}"}

    phones = _allowlist_phones()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        channels = data.setdefault("channels", {})
        whatsapp = channels.setdefault("whatsapp", {})
        whatsapp["allowFrom"] = phones
        whatsapp["groupAllowFrom"] = phones
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        logger.info("OpenClaw allowlist synchronisée (%d numéros)", len(phones))
        result: dict[str, Any] = {
            "synced": True,
            "path": str(path),
            "count": len(phones),
        }
        restart = request_openclaw_gateway_restart()
        result["gateway_restart"] = restart
        if restart.get("restarted"):
            result["message"] = restart.get("message")
        elif restart.get("requested") and not restart.get("restarted"):
            result["restart_hint"] = restart.get("reason")
        return result
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("Échec sync OpenClaw")
        return {"synced": False, "reason": str(exc)}
