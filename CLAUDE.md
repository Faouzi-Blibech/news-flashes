# news-flashes — Project Context for Claude Code

## Purpose

FX advisory news-flash automation system for a forex desk serving French-speaking clients. When a market-moving event occurs (economic calendar or news headline), the system drafts a short French analytical email ("news flash") in house style, routes it through mandatory analyst review, and sends it to a client list.

## Pipeline

```
scheduler → ingestion (calendar + news) → trigger filter → market data
          → Claude draft → review (edit + approve) → HTML email send → audit log
```

Each flash is one SQLite row with status: `candidate → draft → approved → sent` (or `rejected`).

**Mandatory human edit before approval is a compliance requirement.** `draft → sent` is explicitly forbidden. Every send path requires `approved` state.

## Repo Layout

```
src/news_flashes/
  config.py                  # Settings from .env (Pydantic BaseSettings)
  models/
    schema.py                # Shared contract: Event, MarketContext, Flash, Client
    db.py                    # SQLite engine + session (SQLAlchemy)
  ingestion/
    calendar.py              # Forex Factory → Event
    news.py                  # NewsAPI → Event
    market_data.py           # Twelve Data → MarketContext + history
  triggers/
    rules.py                 # High-impact + basket-currency filter + dedup
  scheduler/
    worker.py                # APScheduler: ingest → filter → write candidates
  generation/
    prompt.py                # System prompt + build_user_message()
    generator.py             # Claude API call → draft_text, sets status=draft
    templates/
      example_flash_fr.md    # House-style few-shot example
      disclaimer.txt         # Legal footer (strip the leading # comment line)
  delivery/
    charts.py                # matplotlib history chart → PNG bytes
    render.py                # Jinja2 HTML email renderer
    sender.py                # EmailSender protocol + StubSender
    clients.py               # load_clients(), import_clients_from_csv()
    templates/
      email.html.j2          # Branded HTML email template
  review/
    app.py                   # Streamlit dashboard (list → edit → approve → send)
    logic.py                 # can_approve(), apply_approval(), actionable_statuses()
tests/
seed_candidate.py            # Seeds one realistic DXY/CPI candidate for dev/testing
.env.example
pyproject.toml
```

## How to Run

**Setup:**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env   # fill in API keys
python -c "from news_flashes.models.db import init_db; init_db()"
```

**Seed a test candidate (so dashboard has something to show):**
```powershell
python seed_candidate.py
```

**Review dashboard:**
```powershell
streamlit run src/news_flashes/review/app.py
```

**Ingestion scheduler (polls every 15 min):**
```powershell
python src/news_flashes/scheduler/worker.py
```

**Tests:**
```powershell
pytest
```

## Environment Variables (.env)

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API — draft generation |
| `NEWS_API_KEY` | Yes | NewsAPI.org (free tier) |
| `MARKET_DATA_API_KEY` | Yes | Twelve Data (free tier) |
| `BREVO_API_KEY` | No | Email delivery (300/day free); if unset, StubSender writes to `outbox/` |
| `DATABASE_URL` | No | Defaults to `sqlite:///news_flashes.db` |
| `POLL_INTERVAL_MINUTES` | No | Scheduler interval, default 15 |
| `BASKET_CURRENCIES` | No | Comma-separated, default `USD,EUR,JPY,TND` |

## Key Design Decisions

- **Mandatory human edit** — `can_approve()` in `logic.py` and `advance_to()` in `schema.py` enforce that an analyst must edit the draft before it can be approved. Compliance requirement; do not bypass.
- **Status transition guard** — `Flash.advance_to()` enforces `ALLOWED_TRANSITIONS`; invalid transitions raise. `draft → sent` is explicitly disallowed.
- **StubSender** — used during dev/testing; writes rendered HTML to `outbox/`. Swap to `BrevoSender` in production by setting `BREVO_API_KEY`.
- **No auto-send** — no code path sends without explicit `approved` state.
- **Shared contract** — `models/schema.py` is the boundary between the two dev roles. Changes here require coordination.
- **Two-person team split** — Person A owns `ingestion/`, `triggers/`, `scheduler/`; Person B owns `generation/`, `delivery/`, `review/`.

## Claude Models

- `claude-sonnet-4-6` — routine drafts (`settings.model_default`)
- `claude-opus-4-8` — high-stakes flashes (`settings.model_highstakes`)

Model selection lives in `config.py` via `Settings`. Generation logic in `generation/generator.py`.

## Current Phase

| Phase | Description | Status |
|---|---|---|
| 0 | Scaffold + shared contract (`models/schema.py`) | Done |
| 1 | Walking skeleton — one manual end-to-end flash via dashboard | In progress |
| 2 | Automation — live scheduler, auto-generated charts | Not started |
| 3 | Hardening — audit log, client segmentation, deploy | Not started |

For Phase 1 dev: use `seed_candidate.py` to inject a test candidate, then run the Streamlit dashboard to draft → edit → approve → send (stub).

## Data Flow Detail

1. **Ingestion** — `calendar.py` fetches Forex Factory high-impact events; `news.py` fetches NewsAPI headlines. Both produce `Event` objects.
2. **Trigger filter** — `rules.py` filters to high-impact events touching basket currencies, deduplicates by event ID, writes `Flash` rows with `status=candidate`.
3. **Market data** — `market_data.py` fetches live DXY, USD/JPY, EUR/USD quotes + recent history from Twelve Data, returns `MarketContext`.
4. **Draft generation** — `generator.py` calls Claude with system prompt (house style, French, analytical tone) + `build_user_message()` (event + market context + few-shot example). Stores result as `draft_text`, sets `status=draft`.
5. **Review** — analyst opens Streamlit dashboard, edits draft text, clicks Approve. `apply_approval()` sets `status=approved`, records editor identity + timestamp.
6. **Delivery** — `render.py` renders `email.html.j2` with Jinja2 (includes optional `charts.py` PNG attachment). `sender.py` dispatches via Brevo or StubSender. `status=sent`.
7. **Audit** — all status transitions logged in SQLite with timestamps.
