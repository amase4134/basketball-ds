# NBA Data Pipeline

Local, refreshable NBA analytics warehouse: **`nba_api` → Parquet → DuckDB**.

Built for personal data science projects (shot charts, player archetype clustering). Historical window defaults to the **last 5 NBA seasons**.

## Setup

```bash
cd "/Volumes/Data-700GB/Basketball DS/nba_pipeline"
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Local Explorer

The read-only Explorer runs only on `127.0.0.1`; the browser accesses curated
data exclusively through the FastAPI API.

```bash
# Terminal 1, from nba_pipeline/
source .venv/bin/activate
uvicorn nba_pipeline.web_api.app:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2, from nba_pipeline/web/
npm install
npm run dev
```

Open http://127.0.0.1:5173. For one-process local production mode, build the
frontend and start FastAPI:

```bash
cd web && npm run build && cd ..
uvicorn nba_pipeline.web_api.app:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. The API documents fixed shot-map bins (x −250…250,
y −50…420, 25-coordinate-unit cells) and limits raw player point maps to 5,000
shots; broader results are aggregated server-side.

Or with requirements only:

```bash
pip install -r requirements.txt
pip install -e .
```

## Refresh data

```bash
# Full 5-season backfill (first run) — shot charts take a while
python -m nba_pipeline refresh --all

# Current season only (routine refresh)
python -m nba_pipeline refresh

# One season
python -m nba_pipeline refresh --season 2023-24

# One dataset
python -m nba_pipeline refresh --dataset player_season_stats --all
python -m nba_pipeline refresh --dataset shot_charts --season 2023-24 --force

# Useful flags
python -m nba_pipeline refresh --all --force
python -m nba_pipeline refresh --dataset shot_charts --season 2023-24 --min-minutes 500
python -m nba_pipeline refresh --dataset shot_charts --season 2023-24 --max-shot-players 5  # test pull
python -m nba_pipeline list-seasons
```

Shot charts are pulled only for players with **≥500 total season minutes** in that season’s `player_season_stats` (configurable via `--min-minutes`). Rate stats are per-100 possessions; `MIN` is merged from Totals mode so the filter works as expected.

## Dataset dictionary

| Dataset | Source | Curated path | Notes |
|---|---|---|---|
| `players` | `nba_api.stats.static.players` | `data/curated/players/players.parquet` | Player ID directory |
| `teams` | `nba_api.stats.static.teams` | `data/curated/teams/teams.parquet` | Team directory |
| `player_season_stats` | `LeagueDashPlayerStats` (Per100Possessions rates + Totals `MIN`) | `data/curated/player_season_stats/season=YYYY-YY/` | Archetype clustering; `MIN` is season total minutes |
| `shot_charts` | `ShotChartDetail` (FGA, `team_id=0`) | `data/curated/shot_charts/season=YYYY-YY/` | `LOC_X` / `LOC_Y` shot locations |

Raw API dumps land under `data/raw/`. Refresh outcomes append to `data/meta/refresh_log.parquet`.

### Shot chart columns (curated)

`PLAYER_ID`, `PLAYER_NAME`, `TEAM_ID`, `GAME_ID`, `GAME_DATE`, `EVENT_TYPE`, `SHOT_MADE_FLAG`, `SHOT_TYPE`, `SHOT_DISTANCE`, `LOC_X`, `LOC_Y` (+ hive `season`).

Court coordinates (from NBA API): `LOC_X` ≈ −250…250, `LOC_Y` ≈ −50…400+ (tenths of a foot).

## Query with DuckDB

```python
from nba_pipeline.load.duckdb_views import connect

con = connect()
con.execute("""
  SELECT PLAYER_NAME, AVG(SHOT_MADE_FLAG) AS fg_pct, COUNT(*) AS fga
  FROM shot_charts
  WHERE season = '2023-24'
  GROUP BY 1
  ORDER BY fga DESC
  LIMIT 20
""").fetchdf()
```

See [`notebooks/smoke_test.ipynb`](notebooks/smoke_test.ipynb) for a walkthrough that never hits the API.

## Layout

```
nba_pipeline/
  src/nba_pipeline/
    config.py
    client.py          # retry / sleep
    extract/           # static, player_stats, shot_charts
    transform/clean.py
    load/              # parquet writers, duckdb views
    cli.py
  data/
    raw/
    curated/
    meta/
  notebooks/
```

## Rate limiting

The client sleeps ~0.8s between successful calls and retries with exponential backoff. Be polite to `stats.nba.com` — prefer local Parquet/DuckDB during model iteration.

## Phase 2 (deferred)

Play-by-play / win probability, game logs, scheduled cron refresh, cloud sync.
