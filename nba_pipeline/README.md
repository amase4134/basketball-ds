# NBA Data Pipeline

Local, refreshable NBA analytics warehouse: **`nba_api` → Parquet → DuckDB**.

Built for personal data science projects (shot charts, player archetype clustering, team win
prediction). The warehouse holds the **5 completed seasons before the current one plus the current
season** — so projecting 2026-27 trains on 2021-22 through 2025-26.

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

### Explorer features

- **Multi-player selection.** Add up to 12 players from the typeahead or the
  table checkboxes. The selection drives a side-by-side comparison table with
  the leader marked per metric, and carries over to the Shot Explorer, where
  each player's shots are plotted in their own colour.
- **Per-column filters.** Numeric columns take a min/max range, text columns a
  substring match, and shot result/type a fixed choice. Filters are applied in
  DuckDB, not in the browser.
- **Column sorting and visibility.** Any allow-listed column sorts in either
  direction with a deterministic secondary sort; non-key columns can be hidden.
- **Appearance.** The header theme menu offers the branded palette, a warm
  low-glare *Calm paper* theme, a dimmed *Dusk* theme, and the original
  *Classic* palette, plus comfortable/compact table density. The choice is
  stored per browser.

- **Win Predictor.** A dumbbell chart comparing each team's pre-season projection,
  updated projection, and current 82-game pace; accuracy KPI cards; a season-long
  error trend with a per-team actual-versus-expected trajectory; and a sortable
  30-team table that flags who is beating or trailing their forecast.

Sort, filters, selection, paging, and hidden columns all live in the URL query
string, so any view can be bookmarked or reloaded exactly as configured.

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

## Check data quality

```bash
# Current season: completeness + junk checks, exits non-zero on problems
python -m nba_pipeline check

# One season
python -m nba_pipeline check --season 2023-24

# Also require a successful refresh at or after a timestamp
python -m nba_pipeline check --since 2026-10-09T08:00:00Z
```

Reads curated Parquet and `refresh_log` only — never calls the API. It fails when a
dataset or season partition is missing, the last refresh logged an error, row counts
fall under their floors (players 1,000; teams 30; season partitions non-empty), a pull
comes in under half the previous successful one, or the data is junk: duplicate/null
ids, negative `MIN` or `SHOT_DISTANCE`, `SHOT_MADE_FLAG` outside 0/1, or shot
coordinates outside the court bounds. Court bounds allow a 1% share, since genuine
full-court heaves (~0.1% of a season) clear them.

## Team win predictor

Projects every team's 82-game win total for the current season and scores itself against the games
actually played. Two ridge regressions, both trained on the 5 completed seasons before the target:

| Model | Learns from | Used for |
|---|---|---|
| **Pre-season** | Previous season's efficiency, four factors, and record, plus the strength of the roster carried into the season | A locked 82-game projection, fixed before opening night |
| **In-season profile** | A season's own efficiency profile against its final win total | A live projection that re-reads each team as games are played |

Both are scored by leave-one-season-out cross-validation, and every projection is reconciled so the
league totals 1,230 wins — one team's win is always another's loss.

The live projection blends the two: wins already banked are certain, and the remaining
`82 − GP` games are projected at a rate that shifts from the pre-season prior toward the in-season
profile as the sample grows, reaching full trust at 40 games.

Roster strength follows players, not franchises. Every player on a team's current roster brings
his previous-season per-100 plus-minus, weighted by his previous-season minutes, so a summer trade
moves value from the old team to the new one. Two features come out of it: `ROSTER_PLUS_MINUS`
(that weighted average) and `ROSTER_PROVEN_MINUTES` (the roster's prior-season minutes as a share
of a full season's 19,680 player-minutes). Rookies carry no weight yet. The target season reads the
`rosters` snapshot; completed seasons use the team each player finished with in
`player_season_stats`, since the API cannot reconstruct past opening-night rosters.

```bash
# Fit the models and write the locked pre-season projection
python -m nba_pipeline train-wins --target-season 2026-27

# Re-score against results so far and append a dated snapshot
python -m nba_pipeline check-wins              # refreshes team stats first
python -m nba_pipeline check-wins --offline    # curated Parquet only
```

`train-wins` prints cross-validated accuracy per model and per held-out season, then writes
`data/curated/win_predictions/season=YYYY-YY/` and `data/meta/win_model_metrics.json`.
`check-wins` prints league-wide accuracy plus a team-by-team table, and appends to
`data/curated/win_prediction_history/`, keeping one row per team per day so the whole season's
accuracy can be charted. Re-running on the same day replaces that day's rows rather than
duplicating them.

Tracked per team: games played, actual record, the wins each model expected by this point,
the error against each, the current 82-game pace, and the blended final projection.
League-wide: MAE and RMSE for both models, 82-game pace error, Spearman rank correlation against
the real standings, and playoff-tier hit rate.

Before opening night the NBA API returns no rows for the season at all, so the pipeline writes a
30-team partition at zero games and the tracker reports the locked projection with accuracy
pending.

### Compare against Opta

```bash
python -m nba_pipeline compare-opta              # scrape the Opta Analyst article, then compare
python -m nba_pipeline compare-opta --offline    # re-parse the last saved copy
python -m nba_pipeline compare-opta --url <article>  # a different season's article
```

Scrapes Opta Analyst's [pre-season predictions article](https://theanalyst.com/articles/2026-27-nba-predictions-win-totals-finals-chances)
(projected wins, playoff percentage, and preseason TRACR rank for all 30 teams) and lines it up
against the locked pre-season projection from `train-wins`. It prints an East/West side-by-side
table and writes `data/meta/reports/opta_comparison_<season>_<date>.html` (summary cards, a
dumbbell chart of every team, the biggest disagreements, and per-conference tables) plus a
matching CSV. Agreement is measured as the average gap in wins, Spearman rank correlation,
spread, top-6 and top-10 overlap per conference, and how many teams land in the same tier.
Once games are played it also reports which forecast sits closer to each team's 82-game pace.

The scraper fails loudly if it does not find exactly 30 teams, so a redesigned page cannot
silently produce a partial comparison.

## Weekly email report

A digest of how the predictor is doing, mailed every **Friday at 8:30am**:

```bash
python -m nba_pipeline send-win-report                  # to the configured recipient
python -m nba_pipeline send-win-report --dry-run        # render and archive only
python -m nba_pipeline send-win-report --to me@example.com
```

It covers the accuracy scorecard, the week-over-week change in each metric, the five biggest
over- and underperformers, and the full 30-team table. Each run also archives the HTML to
`data/meta/reports/weekly_win_report_YYYY-MM-DD.html`.

Configure delivery once:

```bash
cp .env.example .env
```

Gmail rejects account passwords over SMTP, so create an **App Password** (requires 2-Step
Verification) at <https://myaccount.google.com/apppasswords>, then paste it into
`SMTP_APP_PASSWORD` in `.env`. The file is gitignored. With no app password set, the report falls
back to macOS `Mail.app` using the account already signed in on this Mac, so delivery works before
any secret is configured.

Install the Friday LaunchAgent:

```bash
cp scripts/com.basketballds.nba-weekly-report.plist ~/Library/LaunchAgents/
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.basketballds.nba-weekly-report.plist

# Status, manual run, logs
launchctl print "gui/$(id -u)/com.basketballds.nba-weekly-report"
launchctl kickstart -p "gui/$(id -u)/com.basketballds.nba-weekly-report"
tail -f logs/weekly_report.log

# Stop it (re-bootstrap to turn it back on)
launchctl bootout "gui/$(id -u)/com.basketballds.nba-weekly-report"
```

## Daily automation

[`scripts/daily_refresh.sh`](scripts/daily_refresh.sh) runs `refresh --force` for the
current season, then `check --since` the run's start time. The check runs even when the
refresh fails, and the script exits non-zero if either step did. A lock directory stops
a second run from starting while a long shot-chart pull is still going. Output lands in
the gitignored `logs/` directory.

A user LaunchAgent runs it at 8:00am local, every day:

```bash
cp scripts/com.basketballds.nba-refresh.plist ~/Library/LaunchAgents/
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.basketballds.nba-refresh.plist

# Status, manual run, logs
launchctl print "gui/$(id -u)/com.basketballds.nba-refresh"
launchctl kickstart -p "gui/$(id -u)/com.basketballds.nba-refresh"
tail -f logs/daily_refresh.log

# Stop it (re-bootstrap to turn it back on)
launchctl bootout "gui/$(id -u)/com.basketballds.nba-refresh"
```

The plist hard-codes the absolute project path, so edit it if the repo moves. If the
external volume is not mounted at 8:00am the script logs that and exits without running.

## Dataset dictionary

| Dataset | Source | Curated path | Notes |
|---|---|---|---|
| `players` | `nba_api.stats.static.players` | `data/curated/players/players.parquet` | Player ID directory |
| `teams` | `nba_api.stats.static.teams` | `data/curated/teams/teams.parquet` | Team directory |
| `player_season_stats` | `LeagueDashPlayerStats` (Per100Possessions rates + Totals `MIN`) | `data/curated/player_season_stats/season=YYYY-YY/` | Archetype clustering; `MIN` is season total minutes |
| `shot_charts` | `ShotChartDetail` (FGA, `team_id=0`) | `data/curated/shot_charts/season=YYYY-YY/` | `LOC_X` / `LOC_Y` shot locations |
| `team_season_stats` | `LeagueDashTeamStats` (Base Per100 + Advanced) | `data/curated/team_season_stats/season=YYYY-YY/` | Win predictor inputs: record, ratings, four factors |
| `rosters` | `CommonAllPlayers` (current season only) | `data/curated/rosters/season=YYYY-YY/` | Which team every rostered player belongs to now; feeds pre-season roster strength |
| `win_predictions` | Derived by `train-wins` / `check-wins` | `data/curated/win_predictions/season=YYYY-YY/` | Current projection and error per team |
| `opta_projections` | Scraped by `compare-opta` from Opta Analyst | `data/curated/opta_projections/season=YYYY-YY/` | Opta projected wins, playoff %, TRACR rank, conference seed; raw HTML under `data/raw/opta_projections/` |
| `win_prediction_history` | Derived by `check-wins` | `data/curated/win_prediction_history/season=YYYY-YY/` | Dated snapshots, one row per team per day |

Raw API dumps land under `data/raw/`. Refresh outcomes append to `data/meta/refresh_log.parquet`,
model accuracy to `data/meta/win_model_metrics.json`, and emailed digests to `data/meta/reports/`.

### Shot chart columns (curated)

`PLAYER_ID`, `PLAYER_NAME`, `TEAM_ID`, `GAME_ID`, `GAME_DATE`, `EVENT_TYPE`, `SHOT_MADE_FLAG`, `SHOT_TYPE`, `SHOT_DISTANCE`, `LOC_X`, `LOC_Y` (+ hive `season`).

Court coordinates (from NBA API): `LOC_X` ≈ −250…250, `LOC_Y` ≈ −50…400+ (tenths of a foot).

### Team stat columns (curated)

Identity and record: `TEAM_ID`, `TEAM_NAME`, `TEAM_ABBREVIATION`, `GP`, `W`, `L`, `W_PCT`.

Efficiency and four factors: `OFF_RATING`, `DEF_RATING`, `NET_RATING`, `PACE`, `TS_PCT`, `EFG_PCT`,
`AST_PCT`, `AST_TO`, `OREB_PCT`, `DREB_PCT`, `REB_PCT`, `TM_TOV_PCT`, `PLUS_MINUS`, plus per-100
box score rates (+ hive `season`).

`FTA_RATE` is derived as `FTA / FGA` — the NBA's own definition — rather than pulled from a third
measure type, which keeps the refresh to two API calls per season.

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
    extract/           # static, player_stats, team_stats, shot_charts
    transform/clean.py
    load/              # parquet writers, duckdb views
    modeling/          # win prediction features, models, accuracy tracking
    reporting/         # weekly report builder + email transport
    web_api/           # read-only FastAPI service
    quality.py         # post-refresh completeness / junk checks
    cli.py
  web/                 # React + Vite Explorer
  data/
    raw/
    curated/
    meta/               # refresh log, model metrics, archived reports
  scripts/             # daily refresh + weekly report scripts & LaunchAgent plists
  tests/
  notebooks/
```

## Rate limiting

The client sleeps ~0.8s between successful calls and retries with exponential backoff. Be polite to `stats.nba.com` — prefer local Parquet/DuckDB during model iteration.

## Phase 2 (deferred)

Play-by-play / win probability, game logs, cloud sync.
