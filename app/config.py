import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://velib:velib123@localhost:5432/velib")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
CITYBIKES_API_URL = os.getenv("CITYBIKES_API_URL", "https://api.citybik.es/v2")
# paris = Vélib Paris uniquement | france = tous réseaux CityBikes FR + JCDecaux optionnel
INGEST_SCOPE = os.getenv("INGEST_SCOPE", "paris").lower()
# Flux GBFS officiel Vélib Métropole (Paris)
VELIB_GBFS_BASE = os.getenv(
    "VELIB_GBFS_BASE",
    "https://velib-metropole-opendata.smovengo.cloud/opendata/Velib_Metropole",
)
JCDECAUX_API_URL = os.getenv("JCDECAUX_API_URL", "https://api.jcdecaux.com/vls/v1")
JCDECAUX_API_KEY = os.getenv("JCDECAUX_API_KEY", "")
JCDECAUX_CONTRACTS = os.getenv("JCDECAUX_CONTRACTS", "")
ENABLE_JCDECAUX = os.getenv("ENABLE_JCDECAUX", "false").lower() in ("1", "true", "yes")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
OPENCLAW_CONFIG_PATH = os.getenv("OPENCLAW_CONFIG_PATH", "")
ALERTS_ADMIN_TOKEN = os.getenv("ALERTS_ADMIN_TOKEN", "")
OPENCLAW_AUTO_RESTART = os.getenv("OPENCLAW_AUTO_RESTART", "true").lower() in ("1", "true", "yes")
OPENCLAW_WHATSAPP_SENDER = os.getenv("OPENCLAW_WHATSAPP_SENDER", "+33781744135")
ALERT_SNOOZE_HOURS = float(os.getenv("ALERT_SNOOZE_HOURS", "6"))
