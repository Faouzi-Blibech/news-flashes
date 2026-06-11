# News Flashes — Build Plan & Team Division

## Context

We run a forex / FX-hedging advisory desk serving French-speaking clients in a TND
(Tunisian Dinar) context. When something important happens in the market, we send clients a
short, well-written analytical email — a "news flash" — like the DXY / USD-JPY note sent on
2026-06-10 (technical levels to watch, why it matters for local TND quotes, a synthesis, a
polite sign-off, and a chart).

Today this is fully manual. We want a system that:

1. **Watches** an economic calendar + market news feed (the "important events" view, like
   TradingView's economic calendar) and flags market-moving items.
2. **Drafts** the French flash automatically with Claude, from the triggering event + live
   FX levels, in the house style of the example email.
3. **Requires a human** to edit and approve every draft (mandatory edit — compliance-safe
   for financial advice). **Nothing sends automatically.**
4. **Sends** the approved, formatted HTML email to the client list and keeps an audit trail.

Repo: `Faouzi-Blibech/news-flashes` (greenfield Python). Team is **2 people**. The work is
split into two parallel tracks that meet at one shared data contract.

**Confirmed decisions** (from our kickoff): triggers = economic calendar + news headlines;
content = AI draft with a *mandatory* analyst edit; sending = human approval required.

---

## Architecture

One-direction pipeline. Each flash is a single SQLite row whose `status` advances through
the stages:

```
[scheduler]   poll every N min
     │
     ▼
[ingestion]   economic calendar + news headlines        → Event(s)
     │
     ▼
[triggers]    keep only high-impact / TND-relevant       → Flash(status=candidate)
     │
     ▼
[market data] fetch DXY, USD/JPY, EUR/USD levels         → MarketContext attached
     │
     ▼
[generation]  Claude drafts French flash                 → Flash(status=draft)
     │
     ▼
[review UI]   analyst reads, EDITS, approves/rejects     → Flash(status=approved)
     │
     ▼
[delivery]    render HTML, send to client list, log      → Flash(status=sent)
```

### Stack (all Python, chosen to be fast for 2 people)

| Concern | Choice | Note |
|---|---|---|
| Economic calendar | **Forex Factory** weekly JSON (`nfs.faireconomy.media/ff_calendar_thisweek.json`) | Free, includes impact level. Trading Economics / FMP = paid upgrade. |
| News headlines | **Marketaux** or **NewsAPI.org** free tier (or FX RSS) | Filter by currency / keywords. |
| FX levels | **Twelve Data** or **Alpha Vantage** free tier (`exchangerate.host` fallback) | Current quote + history (charts). |
| AI draft | **Claude API** — `claude-sonnet-4-6` routine, `claude-opus-4-8` high-stakes | French prose, structured sections. |
| Storage | **SQLite** via SQLModel | `flash` + `client` tables. |
| Review dashboard | **Streamlit** | Fastest edit-and-approve UI. |
| Email HTML | **Jinja2** template | House style + chart + legal disclaimer. |
| Email send | **Brevo** (300/day free) or **Amazon SES** | API key in `.env`. |
| Scheduling | **APScheduler** in-process | Upgrade to a worker later. |
| Charts (Phase 2) | **matplotlib** from history API | Auto DXY / USD-JPY chart. |

### Repo layout

```
src/news_flashes/
  config.py            # settings + env loading            ← SHARED (done)
  models/
    schema.py          # Event, MarketContext, Flash, Client ← SHARED CONTRACT (done)
    db.py              # SQLite engine + session            ← SHARED (done)
  ingestion/           # Person A
    calendar.py        # Forex Factory → Event
    news.py            # news API → Event
    market_data.py     # FX quotes/history → MarketContext
  triggers/
    rules.py           # importance + TND-basket filter + dedup   (Person A)
  scheduler/
    worker.py          # APScheduler: ingest → filter → store     (Person A)
  generation/          # Person B
    prompt.py          # system prompt + house-style (FR)
    templates/         # few-shot example = the 2026-06-10 email
    generator.py       # Claude call → draft text
  delivery/            # Person B
    render.py          # Jinja2 → HTML email
    sender.py          # Brevo/SES send
    clients.py         # client list load/segment
  review/
    app.py             # Streamlit: list, edit, approve, send      (Person B)
tests/
.env.example · pyproject.toml · README.md
```

---

## The shared contract (build together, day 1, before splitting)

The integration boundary is `models/schema.py`. Person A produces; Person B consumes;
neither imports the other's code. Shapes:

- **Event** — `source` (calendar|news), `title`, `currency`, `country`, `impact`,
  `event_time`; calendar extras `actual/forecast/previous`; news extras
  `headline/url/summary`; `dedup_key`.
- **MarketContext** — `quotes`: dict of instrument → `{level, change, asof}` (DXY, USDJPY,
  EURUSD, and any TND-basket pairs).
- **Flash** — `id`, `status` (candidate→draft→approved→sent→rejected), `event` (JSON),
  `market_context` (JSON), `dedup_key`, `draft_text`, `edited_text`, `subject`,
  `created_at`, `approved_by`, `approved_at`, `sent_at`.
- **Client** — `email`, `name`, `segment`, `lang`, `active`.

Status: this file, plus `db.py` and `config.py`, **is already scaffolded** (Phase 0).

---

## How we divide the work (2 people)

Two tracks run in parallel after a shared day-1 contract. Each task below is a concrete,
checkable unit — treat the lists as ordered checklists.

### Person A — "Signals"  (decide *when* to flash and *with what data*)
Owns the **left half**: `ingestion/`, `triggers/`, `scheduler/`.

1. `ingestion/calendar.py` — fetch Forex Factory weekly JSON, map each item to `Event`
   (set `impact`, `currency`, `event_time`, `actual/forecast/previous`, `dedup_key`).
2. `ingestion/news.py` — fetch the news API, keep FX-relevant items, map to `Event`
   (`headline/url/summary`, infer `currency` from keywords).
3. `ingestion/market_data.py` — fetch DXY / USDJPY / EURUSD levels → `MarketContext`.
   (Phase 2: also pull history for charts.)
4. `triggers/rules.py` — **the judgment-heavy part**: keep only high-impact events whose
   currency is in `settings.basket_currencies`; drop duplicates by `dedup_key`. Encode
   "what is actually worth a flash."
5. `scheduler/worker.py` — APScheduler loop every `POLL_INTERVAL_MINUTES`: ingest → filter
   → attach market context → write `Flash(status=candidate)`.
6. Tests: a sample calendar JSON and a sample news payload map to `Event` correctly;
   `rules.py` keeps a high-impact USD event and drops a low-impact one.

**Deliverable to B:** rows in the DB at `status=candidate` with populated `event` +
`market_context`. **Manual handoff for Phase 1:** a tiny `seed_candidate.py` that inserts
one realistic candidate so B can build without the live feeds.

### Person B — "Voice & Delivery"  (turn a flagged event into a sent, approved email)
Owns the **right half**: `generation/`, `delivery/`, `review/`.

1. `generation/templates/` — drop in the 2026-06-10 email as the house-style few-shot
   reference; write the legal disclaimer footer text.
2. `generation/prompt.py` — system prompt enforcing the house style: greeting
   ("Bonsoir chers clients"), numbered sections, level call-outs, "impact on TND quotes",
   synthesis, polite sign-off, **French**.
3. `generation/generator.py` — call Claude with Event+MarketContext, write `draft_text`,
   set `status=draft`. Also propose a `subject`.
4. `review/app.py` — Streamlit dashboard: list drafts, show source event + market data
   side-by-side with an editable text box; **Approve** (requires an edit + sets
   `approved_by`), **Reject**, **Send** buttons.
5. `delivery/render.py` — Jinja2 HTML template: branding, body, chart image slot,
   disclaimer footer.
6. `delivery/clients.py` + `delivery/sender.py` — load/segment the client list; send via
   Brevo/SES; mark `status=sent`, `sent_at`.
7. Tests: a fixed Event+MarketContext produces a French draft with the expected sections;
   no code path advances `draft → sent` without passing through `approved`.

**Consumes from A:** `candidate` / `draft` rows only.

### Shared / either person
`config.py`, `models/` (built together — already scaffolded), `README.md`, the disclaimer
wording (confirm with whoever owns compliance), and each person's own tests.

### Interface = the only thing both must agree on
The DB row contract in `models/schema.py`. As long as A writes valid `candidate` rows and B
reads them, the two tracks never block each other.

---

## Sequencing / timeline

| When | Both | Person A | Person B |
|---|---|---|---|
| **Day 1** | Lock `models/schema.py`, get API keys, `init_db()` | — | — |
| **Days 2–4** | — | calendar + news + market_data → `Event`/`MarketContext`; write `seed_candidate.py` | prompt + generator against a seeded candidate; Streamlit review skeleton |
| **Day 5 (sync)** | **Phase 1 gate:** A seeds a real candidate → B drafts, edits, approves, sends to a test inbox | hand off seed | wire send |
| **Week 2** | Review trigger rules together (shared editorial judgment) | scheduler/worker live polling; charts | per-scenario templates; HTML polish; client segments |
| **Week 2+** | Phase 3: audit log, dedup, disclaimer, deploy, tests | — | — |

**Sync points:** (1) lock the contract day 1; (2) Phase 1 integration on day 5 — one real
flash end-to-end; (3) tune trigger rules together in week 2.

---

## Phases (recap)

- **Phase 0 — Setup:** scaffold + shared contract. **Already done** (`pyproject.toml`,
  `.env.example`, `README.md`, `config.py`, `models/schema.py`, `models/db.py`).
- **Phase 1 — Walking skeleton:** one manually-seeded event → draft → review → send, end to
  end. Defer scheduler + charts. **This is the priority.**
- **Phase 2 — Automation:** turn on the scheduler, tune trigger rules, add auto-charts and
  per-scenario prompt templates (DXY support test, JPY intervention, BCT decision, CPI…).
- **Phase 3 — Hardening:** audit log + compliance disclaimer, dedup, client segmentation,
  deployment, tests.

---

## Decisions to confirm as we go
- **Data-source cost:** free calendar/news/FX tiers are fine for MVP but rate-limited / ToS
  limited — budget a paid provider before production.
- **Charts:** MVP can let the analyst paste a chart screenshot in the review UI; auto-charts
  are Phase 2. Confirm whether day-1 flashes need the chart.
- **Disclaimer / compliance:** add a standing legal footer + keep the approval audit trail;
  confirm exact wording with compliance.
- **Claude model:** start on `claude-sonnet-4-6`; switch high-stakes flashes to
  `claude-opus-4-8` if quality needs it.

---

## Verification

1. **Unit:** ingestion maps sample calendar + news payloads to `Event`; `rules.py` keeps a
   high-impact USD event, drops a low-impact one.
2. **Generation:** fixed Event+MarketContext → French draft with the expected sections.
3. **End-to-end (Phase 1 gate):** seed one event → draft appears in Streamlit → edit a line
   → Approve → send to a **test inbox we control** → HTML renders (chart, disclaimer), DB
   row is `status=sent` with `approved_by` set.
4. **Automation (Phase 2 gate):** start the scheduler, let it poll a live high-impact event,
   confirm a `candidate` appears with no manual injection.
5. **Safety check:** no code path from `draft → sent` that skips the `approved` state.
