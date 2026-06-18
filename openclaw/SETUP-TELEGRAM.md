# Guide — OpenClaw + Telegram + ngrok + cron

## Prérequis

- Docker data lancé : `docker compose up --build` (API sur http://localhost:8000)
- OpenClaw installé (`openclaw --version`)
- ngrok installé (https://ngrok.com)
- Un bot Telegram (via @BotFather)

---

## Étape 1 — Créer le bot Telegram

1. Ouvre Telegram → cherche **@BotFather**
2. Envoie `/newbot` (ou utilise un bot existant)
3. Copie le **token** (format `123456789:ABCdef...`)
4. **Ne partage pas ce token** publiquement

---

## Étape 2 — Récupérer ton Chat ID

**Méthode simple :**

1. Envoie un message à ton bot (ex. `/start`)
2. Ouvre dans le navigateur (remplace `TOKEN`) :
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
3. Cherche `"chat":{"id":` → c'est ton **CHAT_ID** (ex. `123456789`)

---

## Étape 3 — Copier le skill Vélib

PowerShell :

```powershell
$skillSrc = "c:\Users\secre\Desktop\code\projet ecole\Data-Lake\openclaw\skills\velib-analytics"
$skillDst = "$env:USERPROFILE\.openclaw\workspace\skills\velib-analytics"
New-Item -ItemType Directory -Force -Path $skillDst
Copy-Item "$skillSrc\SKILL.md" "$skillDst\SKILL.md" -Force
```

Redémarre le gateway après (étape 6).

---

## Étape 4 — Configurer `openclaw.json`

Fichier : `C:\Users\secre\.openclaw\openclaw.json`

Dans `channels.telegram`, ajoute le webhook (après avoir lancé ngrok, étape 5) :

```json
"telegram": {
  "enabled": true,
  "dmPolicy": "pairing",
  "botToken": "VOTRE_TOKEN_ICI",
  "webhookUrl": "https://XXXX.ngrok-free.app/telegram-webhook",
  "webhookSecret": "un-secret-aleatoire-tres-long-123",
  "groupPolicy": "allowlist",
  "streaming": { "mode": "off" }
}
```

- `webhookSecret` : invente une longue chaîne aléatoire (obligatoire avec webhook)
- `webhookUrl` : URL ngrok + `/telegram-webhook`

**Pairing :** avec `dmPolicy: "pairing"`, la première fois que tu écris au bot, OpenClaw peut demander une approbation. Vérifie avec :

```powershell
openclaw pairing list telegram
openclaw pairing approve telegram <CODE>
```

---

## Étape 5 — ngrok (webhook HTTPS — exigence PDF)

Terminal 1 — gateway OpenClaw :

```powershell
# ou double-clic sur :
C:\Users\secre\.openclaw\gateway.cmd
```

Terminal 2 — ngrok :

```powershell
ngrok http 18789
```

Copie l'URL HTTPS affichée (ex. `https://a1b2c3.ngrok-free.app`).

Mets à jour `webhookUrl` dans `openclaw.json` :

```
https://a1b2c3.ngrok-free.app/telegram-webhook
```

Redémarre le gateway (Ctrl+C puis relance `gateway.cmd`).

---

## Étape 6 — Important : un seul bot à la fois

Si tu utilises **OpenClaw** pour Telegram, le bot Python dans Docker ne doit **pas** faire de long-polling en même temps (même token = conflit).

Option A (recommandée) : OpenClaw gère Telegram, Docker garde seulement l'API data.

Option B : garde le bot Python dans Docker et **désactive** `channels.telegram` dans OpenClaw.

---

## Étape 7 — Créer les 2 jobs cron OpenClaw

Remplace `VOTRE_CHAT_ID` par ton id (étape 2).

### Job 1 — Ingestion toutes les 5 min

Commande directe (sans agent/curl) :

```powershell
node openclaw\install-crons.js
```

Ou :

```powershell
.\openclaw\install-crons.ps1
```

### Job 2 — Alertes KPI → Telegram

Le script `install-crons.js` crée aussi le job qui appelle :

`POST http://localhost:8000/telegram/kpi-alert-check`

(L'API Docker envoie le message Telegram si `TELEGRAM_*` est dans `.env`.)

### Bot Telegram (consultation)

Le skill `velib-analytics` utilise **`web_fetch`** (pas curl). Après mise à jour du skill, **redémarre le gateway**.

Vérifier :

```powershell
openclaw cron list
openclaw cron status
```

---

## Étape 8 — Tester

### Consultation (toi → bot)

Envoie sur Telegram :

- `vélib`
- `quelles stations sont critiques ?`
- `donne moi les KPI`

OpenClaw doit appeler l'API et répondre avec les vraies données.

### Alerte auto

Attends 5 min (cron) ou lance manuellement :

```powershell
openclaw cron run <job-id-alertes>
```

### Ingestion manuelle

```powershell
curl.exe -X POST http://localhost:8000/ingest
```

---

## Dépannage

| Problème | Solution |
|----------|----------|
| Bot ne répond pas | Vérifier gateway + ngrok + webhookUrl |
| `409 Conflict` Telegram | Deux processus utilisent le même token → arrêter le polling Python |
| Pairing bloqué | `openclaw pairing approve telegram ...` |
| Pas de données | `POST /ingest` + attendre 5 min |
| ngrok URL change | Mettre à jour `webhookUrl` à chaque relance ngrok (gratuit) |

---

## Résumé des 2 modes

| Mode | Qui déclenche | Action |
|------|---------------|--------|
| **Consultation** | Toi sur Telegram | OpenClaw → API → réponse |
| **Alerte** | Cron toutes les 5 min | OpenClaw vérifie KPI → message si problème |
