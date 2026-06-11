# news-flashes

AI-drafted, **human-approved** FX market news-flash emails for the advisory desk.

When something market-moving happens (economic-calendar event or news headline), the system
flags it, pulls live FX levels (DXY, USD/JPY, EUR/USD…), and asks Claude to draft a short
French analytical note in the house style. **Nothing is sent automatically** — an analyst
edits and approves every draft in a review dashboard before it goes to clients.

## Pipeline

```
scheduler → ingestion (calendar + news) → trigger filter → market data
          → Claude draft → review (edit + approve) → HTML email send → audit log
```

Each flash is a single row in SQLite whose `status` moves:
`candidate → draft → approved → sent` (or `rejected`).

## Who owns what (2-person team)

| Track | Owner | Modules |
|---|---|---|
| **Signals** (when/with-what-data to flash) | Person A | `ingestion/`, `triggers/`, `scheduler/` |
| **Voice & Delivery** (draft → approve → send) | Person B | `generation/`, `delivery/`, `review/` |
| **Shared contract** (build together, day 1) | Both | `models/`, `config.py` |

The integration boundary is `src/news_flashes/models/schema.py`. Person A produces DB rows at
`status=candidate`; Person B consumes them. Neither track imports the other's code.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows PowerShell:  .venv\Scripts\Activate.ps1
pip install -e ".[dev,charts]"
copy .env.example .env          # then fill in your keys
python -c "from news_flashes.models.db import init_db; init_db()"   # create the SQLite tables
pytest                          # smoke test
```

## Run the review dashboard

```bash
streamlit run src/news_flashes/review/app.py
```

## Status

Phase 0 (scaffold + shared contract) is in place. Phase 1 = get one real flash end-to-end
through the pipeline to a test inbox. See the build plan for phases 2–3.
