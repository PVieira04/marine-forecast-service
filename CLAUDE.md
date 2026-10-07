# Claude Code Instructions

## Architecture — GitHub Actions + Cloudflare Worker cron (Zero Infrastructure)

This project sends twice-daily marine weather forecast emails for a sailing yacht. It runs on GitHub Actions, triggered on time by a free Cloudflare Worker cron, with a send log in Cloudflare D1 — no server, no Docker.

### How It Works
1. A **Cloudflare Worker cron** (`worker/`) dispatches `forecast.yml` at 07:00/19:00 Europe/Madrid; other GitHub Actions workflows run on their own schedules. Both run `run_forecast.py`
2. The script does a brief AIS WebSocket lookup (45s timeout) to get the vessel's position
3. Fetches weather + marine data from Open-Meteo, synoptic charts from DWD
4. Composes an HTML email and sends it via Resend API
5. Updates `position_cache.json` and commits it back to the repo

### Workflows
- **forecast.yml** — Sends morning/evening forecast emails. Primary trigger is `workflow_dispatch` from the Cloudflare Worker (GitHub's own cron starts runs 4-8h late). Its `schedule:` block is a fallback only: it skips if a dispatched run titled `<slot> forecast` succeeded in the last 12h. The `run-name` titles are what the guard matches — keep them in sync.
- **update-position.yml** — Runs every 4 hours (01:30, 09:30, 13:30, 21:30 UTC). AIS position lookup only, no email.
- **keepalive.yml** — Monthly commit to prevent GitHub's 60-day inactivity auto-disable.
- No GitHub watchdog: the Worker checks D1 30 min after each slot instead (see below).
- **ci.yml** — On PRs and main: Worker unit tests, Python import + config load.
- **deploy-worker.yml** — On merge to main touching `worker/`: tests, D1 migrations, `wrangler deploy`. Needs Actions secret `CLOUDFLARE_API_TOKEN` (Workers Scripts: Edit, D1: Edit); skips with a warning without it.
- Runners are pinned to `ubuntu-24.04` (not `ubuntu-latest`) to avoid surprise image migrations.

### Cloudflare Worker (`worker/`)
- `wrangler.toml` — cron `0 5,6,17,18 * * *` (both DST candidates); `src/slot.js` dispatches only when it is 07:00 or 19:00 in Madrid
- Secret `GH_TOKEN` (set with `npx wrangler secret put GH_TOKEN`): fine-grained PAT, this repo only, Actions read & write, no expiration
- Watchdog (`src/watchdog.js`, cron `30 5,6,17,18 * * *`): emails `ALERT_EMAIL` if D1 has no real successful send for the slot. Dispatch failures alert immediately.
- `POST /resend-webhook` (`src/webhook.js`): Svix-signed Resend events → D1 `email_events`; bounces/complaints alert `ALERT_EMAIL`. Secrets: `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `RESEND_WEBHOOK_SECRET`.
- `POST /log` (`src/log.js`): stores each send in D1 database `marine-weather-log` (schema: `migrations/`). Called by `src/send_log.py` with secret `LOG_TOKEN` (= repo Actions secret `SEND_LOG_TOKEN`); URL is `send_log_url` in `config.yaml`. Logging never blocks a send.
- Query: `cd worker && npx wrangler d1 execute marine-weather-log --remote --command "SELECT sent_at, slot, status, location_name FROM sends ORDER BY id DESC LIMIT 10"`
- Logs: Workers observability is on — invocation logs in the Cloudflare dashboard.
- Tests: `cd worker && npm test`; deploy: automatic via deploy-worker.yml, or `cd worker && npx wrangler deploy`; schema changes: add a migration, then `npx wrangler d1 migrations apply marine-weather-log --remote`

### Manual Testing
Trigger a forecast manually from the GitHub Actions UI using `workflow_dispatch`, or run locally:
```bash
# Set environment variables
export RESEND_API_KEY="..."
export RESEND_FROM_EMAIL="..."
export AISSTREAM_API_KEY="..."

# Run forecast
python run_forecast.py --evening
python run_forecast.py --morning
python run_forecast.py --position-only
```

### Configuration
- **`config.yaml`** — Vessel info, recipient email, schedule, forecast settings, chart preferences, default position, optional destination
- **`position_cache.json`** — Last known AIS position, tracked in git, updated by workflows
- **GitHub Secrets** (set in repo Settings > Secrets > Actions):
  - `RESEND_API_KEY` — Email service API key
  - `RESEND_FROM_EMAIL` — Verified sender email address
  - `AISSTREAM_API_KEY` — AIS tracking API key
  - `SEND_LOG_TOKEN` — bearer token for the Worker's `/log` endpoint (same value as the Worker's `LOG_TOKEN`)

### Key Files
- `run_forecast.py` — Main entry point (CLI script)
- `config.yaml` — All configuration
- `src/weather.py` — Open-Meteo API client with retry logic
- `src/charts.py` — DWD synoptic chart fetcher + image compression
- `src/composer.py` — Email composition + Jinja2 rendering
- `src/sender.py` — Resend email API client with retry logic (returns the Resend email id). On failure the recipient gets a short "forecast delayed" notice and `alert_email` gets the details
- `src/send_log.py` — Posts each send to the Worker's D1 send log
- `src/utils.py` — Unit conversion and formatting utilities
- `templates/forecast.html` — HTML email template

### External APIs (all free)
- **Open-Meteo** — Weather + marine forecasts (no key required)
- **DWD Open Data** — Synoptic pressure charts (no key required)
- **aisstream.io** — Real-time AIS vessel tracking (free API key)
- **Resend** — Email sending (free tier: 100 emails/day)
