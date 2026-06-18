"""Demande de redémarrage automatique du gateway OpenClaw (via fichier déclencheur)."""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import OPENCLAW_AUTO_RESTART, OPENCLAW_CONFIG_PATH

logger = logging.getLogger(__name__)

TRIGGER_FILE = ".gateway-restart-requested"
DONE_FILE = ".gateway-restart-done"
RESTART_TIMEOUT_S = 15


def _config_dir() -> Path | None:
    if not OPENCLAW_CONFIG_PATH:
        return None
    return Path(OPENCLAW_CONFIG_PATH).parent


def request_openclaw_gateway_restart() -> dict[str, Any]:
    """Écrit un fichier déclencheur lu par le watcher PowerShell sur le host Windows."""
    if not OPENCLAW_AUTO_RESTART:
        return {"requested": False, "restarted": False, "reason": "auto_restart désactivé"}

    config_dir = _config_dir()
    if config_dir is None or not config_dir.exists():
        return {"requested": False, "restarted": False, "reason": "dossier OpenClaw introuvable"}

    trigger = config_dir / TRIGGER_FILE
    done = config_dir / DONE_FILE

    try:
        if done.exists():
            done.unlink()
        trigger.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    except OSError as exc:
        logger.exception("Impossible d'écrire le déclencheur de redémarrage")
        return {"requested": False, "restarted": False, "reason": str(exc)}

    deadline = time.monotonic() + RESTART_TIMEOUT_S
    while time.monotonic() < deadline:
        if done.exists():
            try:
                payload = json.loads(done.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {"ok": False, "error": "réponse watcher illisible"}
            finally:
                try:
                    done.unlink()
                except OSError:
                    pass

            if payload.get("ok"):
                return {
                    "requested": True,
                    "restarted": True,
                    "message": "Gateway OpenClaw redémarré automatiquement.",
                }
            return {
                "requested": True,
                "restarted": False,
                "reason": payload.get("error") or "échec redémarrage gateway",
            }
        time.sleep(0.5)

    return {
        "requested": True,
        "restarted": False,
        "reason": (
            "Watcher OpenClaw non actif — lancez openclaw/start-gateway-with-watcher.ps1 "
            "au lieu de gateway.cmd seul."
        ),
    }
