# Data Lakehouse Vélib

Ce projet académique met en œuvre une plateforme data complète autour des données Vélib / CityBikes.

## Architecture
- API Python (FastAPI)
- Ingestion périodique toutes les minutes
- Stockage dans MinIO avec zones `raw`, `staging`, `curated`
- Base PostgreSQL pour la couche structurée
- Vues analytiques pour le pilotage
- Dashboard via Metabase
- Automatisation Telegram pour alertes et KPI

## Services
- API: http://localhost:8000/docs
- Metabase: http://localhost:3000
- MinIO Console: http://localhost:9001
- PostgreSQL: localhost:5432
- dashbord : http://localhost:8000/dashboard

## Lancement
```bash
docker compose up --build
```

## Besoins métier couverts
- Identification des stations les plus sollicitées
- Détection des zones sous-offre
- Repérage des pics journaliers d'utilisation
- Détection des stations critiques
- Alertes automatiques via Telegram
