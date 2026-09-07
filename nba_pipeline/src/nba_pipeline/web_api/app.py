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
from .queries import DataUnavailable, PLAYER_COLUMNS, PLAYER_SORTS, SHOT_SORTS, db, require, rows, safe_execute, shot_where, available

GRID = {"x_min": -250, "x_max": 250, "y_min": -50, "y_max": 420, "cell_size": 25, "point_limit": 5000}
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
            result = {"seasons": seasons(connection), "datasets": datasets, "grid": GRID, "shot_coverage": "Regular-season field-goal attempts for players eligible during extraction."}
            if (cfg.meta_dir / "refresh_log.parquet").exists():
                result["refresh_log"] = rows(connection.execute("SELECT * FROM refresh_log ORDER BY 1 DESC LIMIT 20"))
            return result
    @app.get("/api/v1/lookups/players")
    def player_lookup(q: str = Query(min_length=1, max_length=80), season: str | None = None, limit: int = Query(20, ge=1, le=20)):
        require(cfg, "players")
        with con() as connection:
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
    def players(season: str, q: str | None = Query(None, max_length=80), team_id: int | None = None, min_minutes: float | None = Query(None, ge=0), min_pts: float | None = Query(None, ge=0), max_pts: float | None = Query(None, ge=0), sort: str = "PTS", direction: Literal["asc", "desc"] = "desc", page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)):
        require(cfg, "player_season_stats")
        page_size = checked_page_size(page_size)
        if sort not in PLAYER_SORTS: raise HTTPException(422, "Unsupported player sort column")
        if min_pts is not None and max_pts is not None and min_pts > max_pts: raise HTTPException(422, "min_pts cannot exceed max_pts")
        where, params = ["season = ?"], [season]
        if q: where.append("lower(PLAYER_NAME) LIKE lower(?)"); params.append(f"%{q}%")
        if team_id is not None: where.append("TEAM_ID = ?"); params.append(team_id)
        if min_minutes is not None: where.append("MIN >= ?"); params.append(min_minutes)
        if min_pts is not None: where.append("PTS >= ?"); params.append(min_pts)
        if max_pts is not None: where.append("PTS <= ?"); params.append(max_pts)
        predicate = " WHERE " + " AND ".join(where)
        with con() as connection:
            total = connection.execute("SELECT COUNT(*) FROM player_season_stats" + predicate, params).fetchone()[0]
            data = rows(connection.execute(f"SELECT {PLAYER_COLUMNS} FROM player_season_stats {predicate} ORDER BY {PLAYER_SORTS[sort]} {direction.upper()}, PLAYER_ID, TEAM_ID LIMIT ? OFFSET ?", params + [page_size, (page-1)*page_size]))
            return {"items": data, "meta": {"page": page, "page_size": page_size, "total": total, "filters": {"season": season}}}
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
        metrics = ["GP","MIN","PTS","FGA","FG_PCT","FG3A","FG3_PCT","REB","AST","TOV","STL","BLK","PLUS_MINUS"]
        a,b=selected[0],other[0]
        return {"selected": a, "compare_to": b, "deltas": {m: (a[m]-b[m] if a[m] is not None and b[m] is not None else None) for m in metrics}}
    def filters(season, player_id, team_id, result, shot_type, min_distance, max_distance):
        require(cfg,"shot_charts")
        if min_distance is not None and max_distance is not None and min_distance > max_distance: raise HTTPException(422,"min_distance cannot exceed max_distance")
        return shot_where(season,player_id,team_id,result,shot_type,min_distance,max_distance)
    @app.get("/api/v1/shots/summary")
    def shot_summary(season: str, player_id: int|None=None, team_id: int|None=None, result: Literal['made','missed']|None=None, shot_type: Literal['2PT Field Goal','3PT Field Goal']|None=None, min_distance: int|None=Query(None,ge=0), max_distance:int|None=Query(None,ge=0)):
        where, params=filters(season,player_id,team_id,result,shot_type,min_distance,max_distance)
        with con() as connection:
            item=rows(safe_execute(connection, "SELECT COUNT(*) AS fga, COALESCE(SUM(SHOT_MADE_FLAG),0) AS fgm, SUM(SHOT_MADE_FLAG)::DOUBLE/NULLIF(COUNT(*),0) AS fg_pct, SUM(CASE WHEN SHOT_TYPE='2PT Field Goal' THEN 1 ELSE 0 END) AS two_pa, SUM(CASE WHEN SHOT_TYPE='2PT Field Goal' THEN SHOT_MADE_FLAG ELSE 0 END)::DOUBLE/NULLIF(SUM(CASE WHEN SHOT_TYPE='2PT Field Goal' THEN 1 ELSE 0 END),0) AS two_pct, SUM(CASE WHEN SHOT_TYPE='3PT Field Goal' THEN 1 ELSE 0 END) AS three_pa, SUM(CASE WHEN SHOT_TYPE='3PT Field Goal' THEN SHOT_MADE_FLAG ELSE 0 END)::DOUBLE/NULLIF(SUM(CASE WHEN SHOT_TYPE='3PT Field Goal' THEN 1 ELSE 0 END),0) AS three_pct, AVG(SHOT_DISTANCE) AS avg_distance, COUNT(DISTINCT PLAYER_ID) AS selected_player_count FROM shot_charts"+where,params))[0]
        item.update({"total_matching":item["fga"],"recommended_mode":"points" if player_id is not None and item["fga"]<=5000 else "bins"})
        return item
    @app.get("/api/v1/shots/map")
    def shot_map(season:str, player_id:int|None=None, team_id:int|None=None, result:Literal['made','missed']|None=None, shot_type:Literal['2PT Field Goal','3PT Field Goal']|None=None, min_distance:int|None=Query(None,ge=0), max_distance:int|None=Query(None,ge=0), metric:Literal['attempts','makes','fg_pct']='attempts'):
        where,params=filters(season,player_id,team_id,result,shot_type,min_distance,max_distance)
        with con() as connection:
            total=safe_execute(connection,"SELECT COUNT(*) FROM shot_charts"+where,params).fetchone()[0]
            points = player_id is not None and total<=GRID['point_limit']
            if points:
                data=rows(safe_execute(connection,"SELECT LOC_X, LOC_Y, SHOT_MADE_FLAG, SHOT_DISTANCE, SHOT_TYPE FROM shot_charts"+where+" ORDER BY GAME_DATE, GAME_ID",params))
                return {"mode":"points","items":data,"total_matching":total,"returned":len(data),"truncated":False,"grid":GRID}
            sql="SELECT floor((LOC_X+250)/25)*25-250 AS x, floor((LOC_Y+50)/25)*25-50 AS y, COUNT(*) AS attempts, SUM(SHOT_MADE_FLAG) AS makes, SUM(SHOT_MADE_FLAG)::DOUBLE/NULLIF(COUNT(*),0) AS fg_pct FROM shot_charts"+where+" GROUP BY 1,2 ORDER BY 2,1"
            data=rows(safe_execute(connection,sql,params))
            return {"mode":"bins","metric":metric,"items":data,"total_matching":total,"returned":len(data),"truncated":False,"grid":GRID}
    @app.get("/api/v1/shots")
    def shots(season:str, player_id:int|None=None, team_id:int|None=None, result:Literal['made','missed']|None=None, shot_type:Literal['2PT Field Goal','3PT Field Goal']|None=None, min_distance:int|None=Query(None,ge=0), max_distance:int|None=Query(None,ge=0), sort:str='GAME_DATE', direction:Literal['asc','desc']='desc', page:int=Query(1,ge=1), page_size:int=Query(50,ge=1,le=100)):
        page_size = checked_page_size(page_size)
        if sort not in SHOT_SORTS: raise HTTPException(422,"Unsupported shot sort column")
        where,params=filters(season,player_id,team_id,result,shot_type,min_distance,max_distance)
        with con() as connection:
            total=safe_execute(connection,"SELECT COUNT(*) FROM shot_charts"+where,params).fetchone()[0]
            data=rows(safe_execute(connection,"SELECT PLAYER_ID, PLAYER_NAME, TEAM_ID, GAME_ID, GAME_DATE, EVENT_TYPE, SHOT_MADE_FLAG, SHOT_TYPE, SHOT_DISTANCE, LOC_X, LOC_Y FROM shot_charts"+where+f" ORDER BY {SHOT_SORTS[sort]} {direction.upper()}, GAME_ID LIMIT ? OFFSET ?",params+[page_size,(page-1)*page_size]))
            return {"items":data,"meta":{"page":page,"page_size":page_size,"total":total,"filters":{"season":season}}}
    web_dist=cfg.project_root / 'web' / 'dist'
    if web_dist.exists():
        app.mount('/assets', StaticFiles(directory=web_dist/'assets'), name='assets')
        @app.get('/{path:path}', include_in_schema=False)
        def spa(path: str): return FileResponse(web_dist/'index.html')
    return app

app = create_app()
