# Software Requirements Specification: NBA Local Data Explorer

**Version:** 1.0  
**Status:** Proposed  
**Date:** 2026-09-06  
**Audience:** developer building and using the `nba_pipeline` local analytics UI

## 1. Purpose

Build a locally hosted web application that lets a user explore the curated
datasets produced by `nba_pipeline` through a familiar React user interface.
The application is an exploration and analysis surface over existing local
Parquet files; it is not a replacement for the Python extraction pipeline.

The primary users are the local developer/analyst. The application must run on
the same machine as the repository, work without a cloud database or deployed
service, and use the latest files in `nba_pipeline/data` after each pipeline
refresh.

## 2. Scope

### 2.1 In scope (MVP)

- Browse available NBA seasons and local data freshness.
- Search, filter, sort, and page season-level player statistics.
- View a player’s season summary and compare their selected season to another
  available season.
- Explore individual shots on a half-court chart, filtered by player, season,
  team, make/miss, shot type, and distance.
- Show aggregate shot metrics and spatial summaries for the active filters.
- Present data availability, empty-state, loading, and readable error states.
- Run locally from the repository through a single documented development
  command or a small pair of documented commands.

### 2.2 Explicitly out of scope (MVP)

- Calling `stats.nba.com` or triggering pipeline refreshes from the browser.
- Authentication, multi-user accounts, public deployment, and cloud hosting.
- Editing source data from the UI.
- Play-by-play, win probability, game logs, predictions, and clustering.
- A full ad-hoc SQL editor (may be considered after the MVP).

## 3. Existing Data Contract

The application is read-only against `nba_pipeline/data/curated/` and may read
`data/meta/refresh_log.parquet` for freshness/status display. It must never
read from or mutate `data/raw/`.

| Logical dataset | Local source | Current contents | Required use |
|---|---|---:|---|
| Player directory | `curated/players/players.parquet` | 5,103 rows | Player name lookup and search |
| Team directory | `curated/teams/teams.parquet` | 30 rows | Team labels and team filters |
| Player season stats | `curated/player_season_stats/season=YYYY-YY/data.parquet` | 2,867 rows across five seasons | Tables, player profile, comparisons |
| Shot charts | `curated/shot_charts/season=YYYY-YY/data.parquet` | 1,035,473 rows across five seasons | Shot explorer and aggregates |
| Refresh log | `meta/refresh_log.parquet` | 19 rows when specified | Data-status UI |

Partition season values currently available are `2021-22` through `2025-26`.
The UI must discover seasons from the files/API response rather than hard-code
this range.

### 3.1 Fields the MVP depends on

| Dataset | Required fields |
|---|---|
| `players` | `PLAYER_ID`, `FULL_NAME`, `FIRST_NAME`, `LAST_NAME`, `IS_ACTIVE` |
| `teams` | `TEAM_ID`, `FULL_NAME`, `ABBREVIATION`, `NICKNAME`, `CITY`, `STATE`, `YEAR_FOUNDED` |
| `player_season_stats` | `PLAYER_ID`, `PLAYER_NAME`, `TEAM_ID`, `TEAM_ABBREVIATION`, `AGE`, `GP`, `MIN`, `FGM`, `FGA`, `FG_PCT`, `FG3M`, `FG3A`, `FG3_PCT`, `REB`, `AST`, `TOV`, `STL`, `BLK`, `PTS`, `PLUS_MINUS`, `season` |
| `shot_charts` | `PLAYER_ID`, `PLAYER_NAME`, `TEAM_ID`, `GAME_ID`, `GAME_DATE`, `EVENT_TYPE`, `SHOT_MADE_FLAG`, `SHOT_TYPE`, `SHOT_DISTANCE`, `LOC_X`, `LOC_Y`, `season` |

Notes:

- Stat rates in `player_season_stats` are per 100 possessions, except `MIN`,
  which is a total-season minute value.
- A player can have multiple season-stat rows when traded, because rows include
  `TEAM_ID`. The UI must label the team and not silently collapse rows unless
  the API explicitly provides an aggregate.
- `shot_charts` holds regular-season field-goal attempts only for players who
  met the pipeline’s minutes threshold at extraction time.
- NBA court coordinates use `LOC_X` approximately `-250..250` and `LOC_Y`
  approximately `-50..400+`, in tenths of feet. The visual must transform
  them consistently and retain a visible hoop/baseline orientation.

## 4. Proposed Architecture

The application must use React to render a browser-based HTML interface, but
the browser must **not** load Parquet files directly. A small local API is
required because Parquet querying, file-system access, and safe aggregation
belong on the local server.

```text
                    localhost browser
                           |
                    React + TypeScript
                   (HTML/CSS/SVG/Canvas)
                           |
                    /api over HTTP JSON
                           |
                 Local Python API (FastAPI)
                           |
                       DuckDB queries
                           |
       nba_pipeline/data/curated/*.parquet + meta/refresh_log.parquet
```

### 4.1 Technology decisions

| Layer | Requirement | Rationale |
|---|---|---|
| Front end | React 18+ with TypeScript, Vite, React Router | Standard local React workflow and fast iteration |
| UI styling | CSS Modules or a small global CSS layer | Keep the application portable and dependency-light |
| Tables | TanStack Table or equivalent | Server-side paging/sorting/filtering support |
| Data fetching | TanStack Query or equivalent | Caching, request state, and retry controls |
| Charts | SVG for the half court; Canvas/WebGL only if density requires it | Accurate, inspectable court geometry and good MVP performance |
| API | FastAPI with Pydantic response models | Reuses the existing Python project and provides typed JSON contracts |
| Query engine | Existing DuckDB + `nba_pipeline.load.duckdb_views.connect()` | Queries Parquet locally with hive-partition discovery |
| Hosting | `127.0.0.1` only by default | No authentication is needed for a single-user local tool |

### 4.2 Repository layout

```text
nba_pipeline/
  src/nba_pipeline/
    web_api/                 # new FastAPI package
      app.py
      models.py
      queries.py
  web/                       # new React/Vite app
    src/
      api/
      components/
      pages/
      features/
      styles/
  data/                      # existing, read-only to the web app
  docs/
    SRS-react-data-explorer.md
```

The API process must set the pipeline project root from its own package/config
and must not depend on the shell’s current directory. The React dev server
must proxy `/api` to the API server in development. A production-local option
may serve the React build from FastAPI, allowing one command to start both.

### 4.3 API and query rules

- Use parameterized DuckDB SQL for every user-supplied filter and validate
  enum/range values before querying.
- Register only the existing views. If a dataset is missing, return a typed
  `data_unavailable` response (HTTP 404 or 503 as appropriate), never a stack
  trace.
- Open a short-lived read-only DuckDB connection per request, or use a
  thread-safe connection strategy. Do not share a mutable connection across
  concurrent requests without synchronization.
- Select named columns only; do not expose a generic `SELECT *` endpoint.
- Return summary or paged data. The shots endpoint must never return an entire
  season by default.
- Treat Parquet refreshes as external writes. Each request must get a complete
  query result; if a refresh causes a transient read failure, return a retryable
  availability error with a human-readable explanation.

## 5. User Experience and Functional Requirements

### 5.1 Global behavior

- **FR-1:** The home page shall show available seasons, row counts by dataset,
  latest refresh time/status, and links to the Player Stats and Shot Explorer.
- **FR-2:** All user-visible dates, numbers, and percentages shall be formatted
  consistently (for example, `1,234`, `52.4%`, and local date formatting).
- **FR-3:** A chosen season and filters shall be represented in the URL query
  string where practical, so a local view can be reloaded/bookmarked.
- **FR-4:** Every asynchronous view shall show loading, empty, and failure
  states. A failure must name the unavailable dataset and offer a retry action.
- **FR-5:** Controls must be keyboard usable, labelled, and have visible focus
  states. Color alone must not distinguish makes from misses.

### 5.2 Dashboard (`/`)

- **FR-10:** Display the newest available season by default and make the season
  selection change the overview metrics.
- **FR-11:** Display dataset row counts, available partitions, and the last
  relevant `refresh_log` status/message.
- **FR-12:** Show a concise data caveat that shot data covers eligible players
  and regular-season FGA only.

### 5.3 Player statistics explorer (`/players`)

- **FR-20:** Show a server-paged table of player-season-stat rows.
- **FR-21:** Support season selection; player-name search; team selection;
  minimum minutes; and a configurable numeric range for core metrics.
- **FR-22:** Support sorting by any displayed numeric or textual table column,
  with a deterministic secondary sort by `PLAYER_ID` and `TEAM_ID`.
- **FR-23:** Default to the latest season, `MIN >= 0`, descending `PTS`, and a
  page size of 50. Available page sizes are 25, 50, and 100.
- **FR-24:** Include player, team, GP, MIN, PTS, FGA, FG%, 3PA, 3P%, REB, AST,
  TOV, STL, BLK, plus/minus, and age. Users may hide/show non-key columns.
- **FR-25:** Selecting a row or player name shall navigate to the player page,
  retaining the selected season.

### 5.4 Player detail and comparison (`/players/:playerId`)

- **FR-30:** Display a selected player’s available season rows, team context,
  and core stat cards for one selected season.
- **FR-31:** Provide a season trend chart for PTS, REB, AST, and MIN. The UI
  must mark which values are rates versus totals.
- **FR-32:** Allow selection of a comparison season for the same player and
  show a concise delta table for core statistics.
- **FR-33:** Include a direct link to open the shot explorer pre-filtered to
  that player and season when shots are available.

### 5.5 Shot explorer (`/shots`)

- **FR-40:** Provide filters for season, player typeahead, team, make/miss,
  shot type, and inclusive minimum/maximum shot distance.
- **FR-41:** Require at least a season; default to the latest season. If no
  player/team filter is selected, show aggregates and a sampled/aggregated
  court representation, not every individual shot.
- **FR-42:** Display FGA, FGM, FG%, 2PA/2P%, 3PA/3P%, average shot distance,
  and selected-player count for active filters.
- **FR-43:** Render a half-court whose scale/origin produces a correct visual
  mapping for `LOC_X` and `LOC_Y`. Display a legend and accessible text
  alternative for the selected aggregate metrics.
- **FR-44:** For player-scoped results, show individual makes/misses up to a
  documented safe limit (default 5,000). Above the limit, automatically use a
  binned shot-density/FG% view and state that aggregation is active.
- **FR-45:** Let the user choose between attempts, makes, and FG% in the binned
  view. Bins shall use a documented fixed court grid so values are comparable.
- **FR-46:** Provide a paged shot-result table under the court with game date,
  player, event type, made flag, type, distance, and coordinates.
- **FR-47:** Selecting a point/bin shall show its count and metrics; no raw
  player/game detail may be invented when a bin contains several shots.

## 6. API Contract (MVP)

All endpoints are rooted at `/api/v1`, return JSON, and return ISO-8601
timestamps. List endpoints return an object containing `items` and `meta`.

| Endpoint | Parameters | Response purpose |
|---|---|---|
| `GET /health` | none | Service state and curated-data availability |
| `GET /catalog` | none | Datasets, discovered seasons, row counts, latest refresh records |
| `GET /players` | `season`, `q`, `team_id`, `min_minutes`, sortable field/direction, `page`, `page_size` | Paged player stat rows and total count |
| `GET /players/{player_id}` | `season` optional | Player directory data and all available season stat rows |
| `GET /players/{player_id}/comparison` | `season`, `compare_to` | Two explicitly selected stat rows plus deltas |
| `GET /shots/summary` | active shot filters | Aggregate metrics, selected player count, result count, and rendering recommendation |
| `GET /shots/map` | active shot filters, `mode`, `limit` | Point data or fixed-grid bin data, never unbounded raw shots |
| `GET /shots` | active shot filters, sort, `page`, `page_size` | Paged shot table rows |
| `GET /lookups/players` | `q`, `season` optional, `limit` ≤ 20 | Typeahead candidates |
| `GET /lookups/teams` | `season` optional | Team filter choices |

`/players` sort fields shall be allow-listed. `/shots/map` shall return
`mode: "points" | "bins"`, `total_matching`, `returned`, and `truncated` so
the client cannot mistake a safe subset for all matching shots.

Example response envelope:

```json
{
  "items": [],
  "meta": {
    "page": 1,
    "page_size": 50,
    "total": 0,
    "filters": {"season": "2025-26"}
  }
}
```

## 7. Non-functional Requirements

### 7.1 Performance

- **NFR-1:** On the local development machine with warm OS file cache, catalog
  and lookup requests should complete in under 500 ms; player-table and shot
  summary requests should complete in under 1.5 seconds at p95.
- **NFR-2:** A player-scoped point map of 5,000 or fewer shots should render in
  under 1 second after the response is received.
- **NFR-3:** All shot queries must push season and other filters into DuckDB;
  no endpoint may load the million-row shot dataset into a pandas DataFrame to
  filter it in application memory.
- **NFR-4:** API responses for table views should be bounded to a maximum of
  100 rows/page; point-map payloads are bounded to 5,000 points.

### 7.2 Reliability and data integrity

- **NFR-5:** The API shall be read-only with respect to `data/`.
- **NFR-6:** Missing individual season partitions must not prevent the catalog
  or unrelated available seasons from working.
- **NFR-7:** Null or unavailable values must render as `—`, not zero.
- **NFR-8:** Calculated percentages must use `NULLIF(denominator, 0)` and show
  `—` when the denominator is zero.
- **NFR-9:** The application shall make no network request outside localhost in
  normal operation; no telemetry or third-party hosted font is required.

### 7.3 Security and privacy

- **NFR-10:** Bind development services to `127.0.0.1` by default, not all
  interfaces.
- **NFR-11:** CORS in development shall permit only the configured local React
  origin. Production-local single-server mode should not require CORS.
- **NFR-12:** Do not execute client-provided SQL, file paths, or arbitrary sort
  expressions. Validate all query parameters and return `422` for invalid
  values.

### 7.4 Accessibility and responsive behavior

- **NFR-13:** Meet WCAG 2.1 AA for navigation, contrast, focus indicators, and
  form labels where practical for a local analytics tool.
- **NFR-14:** The UI must work at 1280px desktop width and remain usable at
  768px. On narrow widths, filters stack and tables scroll horizontally rather
  than clipping columns.

## 8. Acceptance Criteria

The MVP is complete when all of the following are demonstrably true:

1. With the existing curated files present, a user can start the local UI and
   see all discovered seasons without manually editing configuration.
2. The player table returns the correct filtered row count, page, and sort
   order for a selected season; a user can find a known player by name.
3. A player page shows the player’s available season rows, and comparing two
   seasons produces correct arithmetic deltas for selected core fields.
4. Applying a player and season filter to Shot Explorer returns a summary whose
   FGA equals the count of matching `shot_charts` rows and whose FG% equals
   `SUM(SHOT_MADE_FLAG) / COUNT(*)` (within display rounding).
5. A unfiltered-season Shot Explorer view does not attempt to transmit or draw
   all matching raw shots; it returns/uses the binned representation.
6. With a missing `shot_charts` partition, the player explorer continues to
   work and Shot Explorer shows a clear data-unavailable state.
7. Browser network requests contain only localhost API calls; the API neither
   writes to `data/` nor offers arbitrary SQL/file access.
8. Automated tests cover filter validation, query result calculations, API
   error behavior, and the React loading/empty/error states. A browser test
   covers navigating from a player detail page to a pre-filtered shot view.

## 9. Delivery Plan

### Phase A — service and catalog

Create the FastAPI app, DuckDB query layer, `/health` and `/catalog`, typed
error responses, tests, and local run documentation.

### Phase B — player exploration

Implement the React shell, dashboard, player lookup, server-paged/sorted
player table, player details, and season comparison.

### Phase C — shot exploration

Implement validated shot filters, summary and map endpoints, half-court SVG,
fixed-grid aggregate rendering, point rendering under the safe limit, and the
paged shot table.

### Phase D — hardening

Add keyboard/accessibility pass, responsive layout, query performance checks
against the full local shot data, error-state tests, and a concise README run
guide.

## 10. Open Decisions (to resolve before implementation)

1. Whether a “TOT”/all-team aggregate should be computed for players traded in
   a season, or whether the MVP displays source team rows only. Recommended:
   display source rows first; add an explicitly labelled aggregate later.
2. Whether “team” on shot filters refers to the shot-record `TEAM_ID` only.
   Recommended: yes, and label it as the team associated with that shot record.
3. Whether to include a package such as Recharts for small trend charts or use
   native SVG throughout. Recommended: native SVG for the court and a lightweight
   chart package only if it materially speeds up trend-chart delivery.
4. Whether Phase C bins should use a rectangular court-coordinate grid or NBA
   shot-zone definitions. Recommended: fixed rectangular bins in MVP, followed
   by optional basketball-aware zones when their exact definitions are sourced.
