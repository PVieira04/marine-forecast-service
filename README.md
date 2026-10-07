# Marine Weather Agent

Automated marine weather forecasting with AIS vessel tracking, running on GitHub Actions for free, triggered on time by a free Cloudflare Worker cron.

> **About this repository.** A public snapshot of a private service that has emailed twice-daily forecasts to a real yacht since February 2026. The code is the same; the vessel, recipients, positions and Cloudflare identifiers in `config.yaml` and `worker/wrangler.toml` are placeholders, and the history starts fresh. Replace the placeholders with your own to run it.

## Features

- **Auto-location via AIS**: Tracks vessel position using aisstream.io (6 lookups/day)
- **Position source indicators**: Emails show whether position is from live AIS, stale AIS, or default location
- **Marine data**: Waves, swell height/direction/period from Open-Meteo
- **Synoptic charts**: DWD pressure analysis charts embedded in emails
- **Wind alerts**: Highlights when wind exceeds configurable threshold
- **Twice-daily emails**: Morning (18hr) and evening (36hr) forecasts
- **Destination forecasts**: Optional passage planning with forecast for intended destination
- **Robust API handling**: Automatic retries with exponential backoff for transient failures (including empty responses)
- **Monitoring**: Watchdog alert if a slot's forecast isn't sent; bounce/spam-complaint alerts from Resend webhooks
- **Send log**: Every send (forecast as sent, position, charts) recorded in Cloudflare D1
- **Zero infrastructure**: No server, no Docker — just GitHub Actions and a free Cloudflare Worker + D1

## Vessel

Set in `config.yaml` (the values there are placeholders):

- **Name**: your vessel's name
- **Call Sign** and **MMSI**: from its radio licence; the MMSI is what the AIS lookup follows

## Setup

### 1. Fork/clone the repository

### 2. Add GitHub Secrets

Go to repo Settings > Secrets and variables > Actions, and add:

| Secret | Description |
|--------|-------------|
| `RESEND_API_KEY` | API key from [resend.com](https://resend.com) |
| `RESEND_FROM_EMAIL` | Verified sender email address |
| `AISSTREAM_API_KEY` | API key from [aisstream.io](https://aisstream.io) |
| `SEND_LOG_TOKEN` | Random token shared with the Worker's `LOG_TOKEN` secret (send log) |

### 3. Deploy the Cloudflare Worker cron

GitHub's scheduled runs can start hours late, so a Cloudflare Worker (free plan) triggers the forecast on time.

1. Create a fine-grained personal access token for this repo only, with **Actions: read and write** and no expiration.
2. ```bash
   cd worker
   npx wrangler login
   npx wrangler secret put GH_TOKEN   # paste the token
   npx wrangler deploy
   ```

### 4. Edit config.yaml

Update vessel details, recipient email, schedule, and preferences.

### 5. Push to GitHub

The workflows will start running on their schedules automatically.

## Configuration

All settings are in `config.yaml`:

- **Vessel**: name, call sign, MMSI
- **Recipient**: email address for forecasts
- **Schedule**: timezone, morning/evening hours
- **Forecast**: hours ahead, wind alert threshold
- **Charts**: enable/disable NA and EU synoptic charts
- **Default position**: fallback when AIS is unavailable
- **Destination**: optional passage planning coordinates

## How It Works

### Scheduled Workflows
- **forecast.yml** — 07:00 and 19:00 Europe/Madrid, dispatched by the Cloudflare Worker: fetches weather, composes email, sends forecast. Its own GitHub cron is a late fallback that skips if the Worker's run already sent
- **update-position.yml** — Every 4 hours: AIS position lookup only, updates cache
- **keepalive.yml** — Monthly: prevents GitHub from disabling scheduled workflows
- **Cloudflare Worker watchdog** — 30 min after each slot: alerts if no forecast was sent

### Manual Testing
Trigger any workflow manually from the GitHub Actions tab, or run locally:
```bash
export RESEND_API_KEY="..."
export RESEND_FROM_EMAIL="..."
export AISSTREAM_API_KEY="..."

python run_forecast.py --evening     # Send evening forecast
python run_forecast.py --morning     # Send morning forecast
python run_forecast.py --position-only  # Just update AIS position
```

## Architecture

- **Cloudflare Worker**: On-time cron that dispatches the forecast workflow, plus `POST /log` for the send log
- **Cloudflare D1**: `marine-weather-log` database of every send
- **GitHub Actions**: Runs the forecast and AIS workflows
- **Open-Meteo**: Weather and marine data APIs (free, no key)
- **DWD Open Data**: Synoptic pressure charts (free, no key)
- **aisstream.io**: AIS vessel tracking via WebSocket (free with API key)
- **Resend**: Email delivery (free tier: 100 emails/day)
- **position_cache.json**: Last known vessel position, tracked in git
