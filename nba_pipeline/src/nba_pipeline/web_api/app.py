"""FastAPI application. Bind with: uvicorn nba_pipeline.web_api.app:app --host 127.0.0.1."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from nba_pipeline.config import Settings
from .queries import DataUnavailable, InvalidFilter, MAX_SELECTED_IDS, PLAYER_COLUMNS, PLAYER_LIKES, PLAYER_RANGES, PLAYER_SORTS, SHOT_RANGES, SHOT_SORTS, contains_where, db, id_list, parse_bounds, range_where, require, rows, safe_execute, shot_where, available

GRID = {"x_min": -250, "x_max": 250, "y_min": -50, "y_max": 420, "cell_size": 25, "point_limit": 5000}
COMPARE_METRICS = ["GP", "MIN", "PTS", "FGA", "FG_PCT", "FG3A", "FG3_PCT", "REB", "AST", "TOV", "STL", "BLK", "PLUS_MINUS"]
LOWER_IS_BETTER = {"TOV"}
def checked_page_size(value: int) -> int:
    if value not in (25, 50, 100):
        raise HTTPException(422, "page_size must be 25, 50, or 100")
    return value

def create_app(cfg: Settings | None = None) -> FastAPI:
    cfg = cfg or Settings()
    app = FastAPI(title="NBA Local Data Explorer", version="1.0")
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173"], allow_methods=["GET"], allow_headers=["*"])
    @app.exception_handler(DataUnavailable)
    async def unavailable(_: Request, exc: DataUnavailable):
        return JSONResponse(status_code=503, content={"error": {"code": "data_unavailable", "dataset": exc.dataset, "message": exc.detail, "retryable": True}})
    @app.exception_handler(InvalidFilter)
    async def invalid_filter(_: Request, exc: InvalidFilter):
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    @contextmanager
    def con():
        connection = db(cfg)
        try: yield connection
        finally: connection.close()
    def seasons(connection):
        require(cfg, "player_season_stats")
        return [r[0] for r in connection.execute("SELECT DISTINCT season FROM player_season_stats ORDER BY season DESC").fetchall()]
    @app.get("/api/v1/health")
    def health(): return {"status": "ok", "datasets": {d: available(cfg, d) for d in ("players", "teams", "player_season_stats", "shot_charts")}}
    @app.get("/api/v1/catalog")
    def catalog():
        with con() as connection:
            datasets = {}
            for name in ("players", "teams", "player_season_stats", "shot_charts"):
                if available(cfg, name):
                    datasets[name] = {"available": True, "rows": connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]}
                else: datasets[name] = {"available": False, "rows": 0}
            result = {"seasons": seasons(connection), "datasets": datasets, "grid": GRID, "limits": {"max_selected_players": MAX_SELECTED_IDS, "page_sizes": [25, 50, 100]}, "shot_coverage": "Regular-season field-goal attempts for players eligible during extraction."}
            if (cfg.meta_dir / "refresh_log.parquet").exists():
                result["refresh_log"] = rows(connection.execute("SELECT dataset, season, row_count, status, message, timestamp FROM refresh_log ORDER BY timestamp DESC LIMIT 20"))
            return result
    @app.get("/api/v1/lookups/players")
    def player_lookup(q: str | None = Query(None, min_length=1, max_length=80), ids: str | None = Query(None, description="Resolve a comma-separated PLAYER_ID selection to names"), season: str | None = None, limit: int = Query(20, ge=1, le=20)):
        require(cfg, "players")
        selected = id_list(ids)
        if not q and not selected: raise HTTPException(422, "Provide either q or ids")
        with con() as connection:
            if selected:
                return {"items": rows(connection.execute(f"SELECT PLAYER_ID, FULL_NAME FROM players WHERE PLAYER_ID IN ({', '.join('?' * len(selected))}) ORDER BY FULL_NAME", selected))}
            if season and available(cfg, "player_season_stats"):
                return {"items": rows(connection.execute("SELECT DISTINCT p.PLAYER_ID, p.FULL_NAME FROM players p JOIN player_season_stats s USING (PLAYER_ID) WHERE s.season = ? AND lower(p.FULL_NAME) LIKE lower(?) ORDER BY p.FULL_NAME LIMIT ?", [season, f"%{q}%", limit]))}
            return {"items": rows(connection.execute("SELECT PLAYER_ID, FULL_NAME FROM players WHERE lower(FULL_NAME) LIKE lower(?) ORDER BY FULL_NAME LIMIT ?", [f"%{q}%", limit]))}
    @app.get("/api/v1/lookups/teams")
    def team_lookup(season: str | None = None):
        require(cfg, "teams")
        with con() as connection:
            sql, params = "SELECT TEAM_ID, FULL_NAME, ABBREVIATION FROM teams", []
            if season and available(cfg, "player_season_stats"):
                sql = "SELECT DISTINCT t.TEAM_ID, t.FULL_NAME, t.ABBREVIATION FROM teams t JOIN player_season_stats s USING (TEAM_ID) WHERE s.season = ?"; params=[season]
            return {"items": rows(connection.execute(sql + " ORDER BY FULL_NAME", params))}
    @app.get("/api/v1/players")
    def players(season: str, q: str | None = Query(None, max_length=80), player_ids: str | None = Query(None, description="Comma-separated PLAYER_ID selection"), team_id: int | None = None, team_ids: str | None = Query(None, description="Comma-separated TEAM_ID selection"), min_minutes: float | None = Query(None, ge=0), min_stat: list[str] | None = Query(None, description="Repeated COLUMN:VALUE lower bounds"), max_stat: list[str] | None = Query(None, description="Repeated COLUMN:VALUE upper bounds"), contains: list[str] | None = Query(None, description="Repeated COLUMN:TEXT substring filters"), sort: str = "PTS", direction: Literal["asc", "desc"] = "desc", page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)):
        require(cfg, "player_season_stats")
        page_size = checked_page_size(page_size)
        if sort not in PLAYER_SORTS: raise HTTPException(422, "Unsupported player sort column")
        selected_players, selected_teams = id_list(player_ids), id_list(team_ids)
        if team_id is not None and team_id not in selected_teams: selected_teams.append(team_id)
        where, params = ["season = ?"], [season]
        if q: where.append("lower(PLAYER_NAME) LIKE lower(?)"); params.append(f"%{q}%")
        if selected_players: where.append(f"PLAYER_ID IN ({', '.join('?' * len(selected_players))})"); params.extend(selected_players)
        if selected_teams: where.append(f"TEAM_ID IN ({', '.join('?' * len(selected_teams))})"); params.extend(selected_teams)
        if min_minutes is not None: where.append("MIN >= ?"); params.append(min_minutes)
        range_clauses, range_params = range_where(parse_bounds(min_stat, PLAYER_RANGES), parse_bounds(max_stat, PLAYER_RANGES), PLAYER_RANGES)
        where.extend(range_clauses); params.extend(range_params)
        like_clauses, like_params = contains_where(contains, PLAYER_LIKES)
        where.extend(like_clauses); params.extend(like_params)
        predicate = " WHERE " + " AND ".join(where)
        with con() as connection:
            total = connection.execute("SELECT COUNT(*) FROM player_season_stats" + predicate, params).fetchone()[0]
            data = rows(connection.execute(f"SELECT {PLAYER_COLUMNS} FROM player_season_stats {predicate} ORDER BY {PLAYER_SORTS[sort]} {direction.upper()} NULLS LAST, PLAYER_ID, TEAM_ID LIMIT ? OFFSET ?", params + [page_size, (page-1)*page_size]))
            return {"items": data, "meta": {"page": page, "page_size": page_size, "total": total, "sort": sort, "direction": direction, "filters": {"season": season, "player_ids": selected_players, "team_ids": selected_teams}}}
    @app.get("/api/v1/players/compare")
    def compare_players(season: str, player_ids: str = Query(description="Comma-separated PLAYER_ID selection")):
        require(cfg, "player_season_stats")
        selected = id_list(player_ids)
        if len(selected) < 2: raise HTTPException(422, "Select at least two players to compare")
        with con() as connection:
            data = rows(connection.execute(f"SELECT {PLAYER_COLUMNS} FROM player_season_stats WHERE season = ? AND PLAYER_ID IN ({', '.join('?' * len(selected))}) ORDER BY PTS DESC NULLS LAST, PLAYER_ID, TEAM_ID", [season, *selected]))
        if not data: raise HTTPException(404, "No stat rows for the selected players and season")
        leaders = {}
        for metric in COMPARE_METRICS:
            scored = [r for r in data if r[metric] is not None]
            if scored:
                best = min(scored, key=lambda r: r[metric]) if metric in LOWER_IS_BETTER else max(scored, key=lambda r: r[metric])
                leaders[metric] = {"PLAYER_ID": best["PLAYER_ID"], "TEAM_ID": best["TEAM_ID"], "value": best[metric], "lower_is_better": metric in LOWER_IS_BETTER}
        return {"items": data, "leaders": leaders, "metrics": COMPARE_METRICS, "meta": {"season": season, "requested": selected, "returned": len(data)}}
    @app.get("/api/v1/players/{player_id}")
    def player_detail(player_id: int, season: str | None = None):
        require(cfg, "player_season_stats")
        with con() as connection:
            directory = rows(connection.execute("SELECT PLAYER_ID, FULL_NAME, FIRST_NAME, LAST_NAME, IS_ACTIVE FROM players WHERE PLAYER_ID = ?", [player_id])) if available(cfg,"players") else []
            stat_rows = rows(connection.execute(f"SELECT {PLAYER_COLUMNS} FROM player_season_stats WHERE PLAYER_ID = ?" + (" AND season = ?" if season else "") + " ORDER BY season DESC, TEAM_ID", [player_id] + ([season] if season else [])))
            if not directory and not stat_rows: raise HTTPException(404, "Player not found")
            return {"player": directory[0] if directory else {"PLAYER_ID": player_id}, "season_rows": stat_rows}
    @app.get("/api/v1/players/{player_id}/comparison")
    def comparison(player_id: int, season: str, compare_to: str):
        require(cfg, "player_season_stats")
        with con() as connection:
            selected = rows(connection.execute(f"SELECT {PLAYER_COLUMNS} FROM player_season_stats WHERE PLAYER_ID=? AND season=? ORDER BY TEAM_ID", [player_id,season]))
            other = rows(connection.execute(f"SELECT {PLAYER_COLUMNS} FROM player_season_stats WHERE PLAYER_ID=? AND season=? ORDER BY TEAM_ID", [player_id,compare_to]))
        if not selected or not other: raise HTTPException(404, "One selected season is unavailable")
        a,b=selected[0],other[0]
        return {"selected": a, "compare_to": b, "deltas": {m: (a[m]-b[m] if a[m] is not None and b[m] is not None else None) for m in COMPARE_METRICS}}
    def filters(season, player_id, player_ids, team_id, team_ids, result, shot_type, min_distance, max_distance, min_stat=None, max_stat=None):
        require(cfg,"shot_charts")
        if min_distance is not None and max_distance is not None and min_distance > max_distance: raise HTTPException(422,"min_distance cannot exceed max_distance")
        players_selected, teams_selected = id_list(player_ids), id_list(team_ids)
        if player_id is not None and player_id not in players_selected: players_selected.append(player_id)
        if team_id is not None and team_id not in teams_selected: teams_selected.append(team_id)
        where, params = shot_where(season,players_selected,teams_selected,result,shot_type,min_distance,max_distance)
        range_clauses, range_params = range_where(parse_bounds(min_stat, SHOT_RANGES), parse_bounds(max_stat, SHOT_RANGES), SHOT_RANGES)
        if range_clauses: where += " AND " + " AND ".join(range_clauses); params.extend(range_params)
        return where, params, players_selected
    @app.get("/api/v1/shots/summary")
    def shot_summary(season: str, player_id: int|None=None, player_ids: str|None=None, team_id: int|None=None, team_ids: str|None=None, result: Literal['made','missed']|None=None, shot_type: Literal['2PT Field Goal','3PT Field Goal']|None=None, min_distance: int|None=Query(None,ge=0), max_distance:int|None=Query(None,ge=0)):
        where, params, selected=filters(season,player_id,player_ids,team_id,team_ids,result,shot_type,min_distance,max_distance)
        with con() as connection:
            item=rows(safe_execute(connection, "SELECT COUNT(*) AS fga, COALESCE(SUM(SHOT_MADE_FLAG),0) AS fgm, SUM(SHOT_MADE_FLAG)::DOUBLE/NULLIF(COUNT(*),0) AS fg_pct, SUM(CASE WHEN SHOT_TYPE='2PT Field Goal' THEN 1 ELSE 0 END) AS two_pa, SUM(CASE WHEN SHOT_TYPE='2PT Field Goal' THEN SHOT_MADE_FLAG ELSE 0 END)::DOUBLE/NULLIF(SUM(CASE WHEN SHOT_TYPE='2PT Field Goal' THEN 1 ELSE 0 END),0) AS two_pct, SUM(CASE WHEN SHOT_TYPE='3PT Field Goal' THEN 1 ELSE 0 END) AS three_pa, SUM(CASE WHEN SHOT_TYPE='3PT Field Goal' THEN SHOT_MADE_FLAG ELSE 0 END)::DOUBLE/NULLIF(SUM(CASE WHEN SHOT_TYPE='3PT Field Goal' THEN 1 ELSE 0 END),0) AS three_pct, AVG(SHOT_DISTANCE) AS avg_distance, COUNT(DISTINCT PLAYER_ID) AS selected_player_count FROM shot_charts"+where,params))[0]
        item.update({"total_matching":item["fga"],"recommended_mode":"points" if selected and item["fga"]<=GRID['point_limit'] else "bins"})
        return item
    @app.get("/api/v1/shots/map")
    def shot_map(season:str, player_id:int|None=None, player_ids:str|None=None, team_id:int|None=None, team_ids:str|None=None, result:Literal['made','missed']|None=None, shot_type:Literal['2PT Field Goal','3PT Field Goal']|None=None, min_distance:int|None=Query(None,ge=0), max_distance:int|None=Query(None,ge=0), metric:Literal['attempts','makes','fg_pct']='attempts'):
        where,params,selected=filters(season,player_id,player_ids,team_id,team_ids,result,shot_type,min_distance,max_distance)
        with con() as connection:
            total=safe_execute(connection,"SELECT COUNT(*) FROM shot_charts"+where,params).fetchone()[0]
            if selected and total<=GRID['point_limit']:
                data=rows(safe_execute(connection,"SELECT PLAYER_ID, PLAYER_NAME, LOC_X, LOC_Y, SHOT_MADE_FLAG, SHOT_DISTANCE, SHOT_TYPE FROM shot_charts"+where+" ORDER BY GAME_DATE, GAME_ID",params))
                return {"mode":"points","items":data,"total_matching":total,"returned":len(data),"truncated":False,"grid":GRID,"player_ids":selected}
            sql="SELECT floor((LOC_X+250)/25)*25-250 AS x, floor((LOC_Y+50)/25)*25-50 AS y, COUNT(*) AS attempts, SUM(SHOT_MADE_FLAG) AS makes, SUM(SHOT_MADE_FLAG)::DOUBLE/NULLIF(COUNT(*),0) AS fg_pct FROM shot_charts"+where+" GROUP BY 1,2 ORDER BY 2,1"
            data=rows(safe_execute(connection,sql,params))
            return {"mode":"bins","metric":metric,"items":data,"total_matching":total,"returned":len(data),"truncated":False,"grid":GRID,"player_ids":selected}
    @app.get("/api/v1/shots")
    def shots(season:str, player_id:int|None=None, player_ids:str|None=None, team_id:int|None=None, team_ids:str|None=None, result:Literal['made','missed']|None=None, shot_type:Literal['2PT Field Goal','3PT Field Goal']|None=None, min_distance:int|None=Query(None,ge=0), max_distance:int|None=Query(None,ge=0), min_stat:list[str]|None=Query(None), max_stat:list[str]|None=Query(None), sort:str='GAME_DATE', direction:Literal['asc','desc']='desc', page:int=Query(1,ge=1), page_size:int=Query(50,ge=1,le=100)):
        page_size = checked_page_size(page_size)
        if sort not in SHOT_SORTS: raise HTTPException(422,"Unsupported shot sort column")
        where,params,selected=filters(season,player_id,player_ids,team_id,team_ids,result,shot_type,min_distance,max_distance,min_stat,max_stat)
        with con() as connection:
            total=safe_execute(connection,"SELECT COUNT(*) FROM shot_charts"+where,params).fetchone()[0]
            data=rows(safe_execute(connection,"SELECT PLAYER_ID, PLAYER_NAME, TEAM_ID, GAME_ID, GAME_DATE, EVENT_TYPE, SHOT_MADE_FLAG, SHOT_TYPE, SHOT_DISTANCE, LOC_X, LOC_Y FROM shot_charts"+where+f" ORDER BY {SHOT_SORTS[sort]} {direction.upper()} NULLS LAST, GAME_ID LIMIT ? OFFSET ?",params+[page_size,(page-1)*page_size]))
            return {"items":data,"meta":{"page":page,"page_size":page_size,"total":total,"sort":sort,"direction":direction,"filters":{"season":season,"player_ids":selected}}}
    web_dist=cfg.project_root / 'web' / 'dist'
    if web_dist.exists():
        app.mount('/assets', StaticFiles(directory=web_dist/'assets'), name='assets')
        @app.get('/{path:path}', include_in_schema=False)
        def spa(path: str):
            if path:
                candidate = (web_dist / path).resolve()
                if candidate.is_file() and candidate.is_relative_to(web_dist.resolve()):
                    return FileResponse(candidate)
            return FileResponse(web_dist/'index.html')
    return app

app = create_app()
