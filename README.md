# Data Lakehouse Vélib

Plateforme data complète autour des données de vélos en libre-service (CityBikes API).
Conçue pour répondre à des besoins métier réels : pilotage opérationnel, détection d'incidents, planification des équipes et amélioration du service.

---

## Architecture

```
CityBikes API
     │
     ▼
┌─────────────────────────────────────────────┐
│              FastAPI (Python)               │
│  Ingestion toutes les 5 min (rate-limit ok) │
└──────────┬─────────────────┬───────────────┘
           │                 │
           ▼                 ▼
     ┌──────────┐     ┌─────────────┐
     │  MinIO   │     │ PostgreSQL  │
     │  raw     │     │  networks   │
     │  staging │     │  stations   │
     │  curated │     │  snapshots  │
     └──────────┘     └──────┬──────┘
                             │
                    ┌────────▼────────┐
                    │  Vues SQL (8)   │
                    │  analytiques    │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        Dashboard       Metabase        Telegram
     (Plotly+Leaflet)    BI Tool          Bot
```

---

## Services

| Service | URL | Description |
|---|---|---|
| **Dashboard** | http://localhost:8000/dashboard | Dashboard temps réel principal |
| **API Docs** | http://localhost:8000/docs | Documentation FastAPI interactive |
| **Metabase** | http://localhost:3000 | BI tool (source : `db`, user : `velib`) |
| **MinIO Console** | http://localhost:9001 | Explorateur Data Lake |
| **PostgreSQL** | localhost:5432 | Base `velib`, user `velib` |

---

## Lancement

```bash
# 1. Copier et remplir les variables d'environnement
cp .env.example .env
# Renseigner TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID

# 2. Démarrer tous les services
docker compose up --build

# 3. Ouvrir le dashboard
# http://localhost:8000/dashboard
```

---

## KPI opérationnels — Questions métier

Ces KPI répondent aux 5 questions métier du cahier des charges.

### Q1 — Quelles stations ont le plus fort taux d'utilisation ?

**Endpoint** : `GET /analytics/top-stations`

Calcule le taux d'utilisation moyen par station sur la dernière heure.
Le taux est défini comme `(capacité - vélos disponibles) / capacité × 100`.

- Visualisation : graphique barres horizontal (rouge > 80%, jaune > 50%, vert sinon)
- Limite configurable (défaut : 15 stations)

---

### Q2 — Zones où l'offre de vélos est insuffisante ?

**Endpoint** : `GET /analytics/insufficient-supply`

Regroupe les stations par zone géographique (arrondi lat/lon à 2 décimales).
Retourne les zones où la moyenne de vélos disponibles est inférieure à 3.

- Visualisation : tableau avec barres de progression
- Critère : `AVG(bikes_available) < 3`

---

### Q3 — Pics d'utilisation journaliers ?

**Endpoint** : `GET /analytics/hourly-peak`

Calcule l'utilisation moyenne et le nombre de vélos disponibles par tranche horaire sur les 7 derniers jours.

- Visualisation : graphique ligne (utilisation) + barres (vélos dispo.)
- Fenêtre : 7 jours d'historique

---

### Q4 — Déséquilibres géographiques de disponibilité ?

**Endpoint** : `GET /analytics/geographic-balance`

Agrège les données par ville et zone GPS. Identifie les zones sur- ou sous-dotées en vélos.

- Visualisation : carte Leaflet interactive (points colorés selon disponibilité)
- Vert : normal | Jaune : < 3 vélos | Rouge : critique

---

### Q5 — Stations critiques nécessitant un rééquilibrage ?

**Endpoint** : `GET /analytics/rebalancing`

Identifie les stations avec un pourcentage élevé d'états critiques (vide ou pleine) sur la dernière heure.

- Visualisation : tableau avec % criticité par station
- Critère : `COUNT(is_critical) / COUNT(*) > 0`

---

## KPI Direction — Amélioration du service

KPI stratégiques destinés au pilotage et à la prise de décision.

### KPI 1 — Taux de fiabilité par station

**Endpoint** : `GET /analytics/station-reliability`

Mesure le pourcentage du temps pendant lequel chaque station est dans un état fonctionnel (`status = 'ok'`), ni vide ni pleine, sur les 6 dernières heures.

- **Décision** : prioriser les stations les moins fiables pour la maintenance ou le rééquilibrage
- Visualisation : graphique barres (rouge < 50%, jaune < 75%, violet sinon)

---

### KPI 2 — Stations fantômes

**Endpoint** : `GET /analytics/ghost-stations`

Détecte les stations dont l'état ne change pas sur une fenêtre de 6 heures : toujours vides ou toujours pleines.

- **Décision** : déclencher un rééquilibrage urgent ; identifier si la capacité installée est adaptée
- Critère : `COUNT(DISTINCT status) = 1` et statut `empty` ou `full` sur au moins 3 mesures

---

### KPI 3 — Efficacité réseau par ville

**Endpoint** : `GET /analytics/city-efficiency`

Calcule pour chaque ville le taux de disponibilité réel : `vélos disponibles / capacité totale`.
Identifie les villes structurellement sous-dotées (en dessous du seuil de 30%).

- **Décision** : investir dans l'ajout de vélos ou de stations dans les villes en dessous du seuil
- Visualisation : graphique barres avec ligne seuil à 30%

---

### KPI 4 — Taux de criticité par heure

**Endpoint** : `GET /analytics/hourly-criticality`

Calcule le pourcentage de stations en état critique par tranche horaire sur les 7 derniers jours.

- **Décision** : planifier les équipes de rééquilibrage sur les créneaux horaires les plus critiques
- Visualisation : courbe % critique + barres stations vides par heure

---

## Automatisations Telegram

| Automatisation | Condition | Fréquence | Action |
|---|---|---|---|
| **Alerte critique** | > 5 stations critiques dans une ville | Toutes les 5 min | Message Telegram avec détail par ville |
| **Alerte saturation** | Taux moyen d'une ville ≥ 85% | Toutes les 5 min | Message avec top 3 stations saturées |
| **Rapport horaire** | À heure pile | Toutes les heures | Bilan national + top 5 réseaux chargés |

### Commandes bot disponibles

| Commande | Description |
|---|---|
| `/kpi` | Vue d'ensemble des réseaux |
| `/critique` | Stations critiques en ce moment |
| `/taux` | Top 15 taux d'utilisation (1h) |
| `/pics` | Pics d'utilisation par heure |
| `/reequilibrage` | Stations à rééquilibrer |

---

## Structure du projet

```
projet_velip/
├── app/
│   ├── main.py          # FastAPI — endpoints + scheduler
│   ├── ingest.py        # Pipeline ingestion CityBikes → MinIO → PostgreSQL
│   ├── analytics.py     # Requêtes SQL analytiques (9 fonctions KPI)
│   ├── telegram_bot.py  # Bot Telegram — commandes + alertes automatiques
│   ├── db.py            # Connexion SQLAlchemy
│   ├── minio_client.py  # Client MinIO (raw / staging / curated)
│   ├── config.py        # Variables d'environnement
│   └── static/
│       └── dashboard.html  # Dashboard Plotly.js + Leaflet
├── schema/
│   ├── init.sql         # Création tables + 8 vues analytiques
│   └── 00_setup.sh      # Script init PostgreSQL
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Variables d'environnement

Copiez `.env.example` en `.env` et renseignez :

```env
TELEGRAM_BOT_TOKEN=   # Token obtenu via @BotFather
TELEGRAM_CHAT_ID=     # Votre chat ID Telegram
```

Les autres variables ont des valeurs par défaut fonctionnelles en local.
