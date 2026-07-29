"""CLI for refreshing NBA Parquet datasets."""

from __future__ import annotations

import logging
from typing import Optional

import typer

from nba_pipeline.config import DATASETS, Settings, default_seasons, settings
from nba_pipeline.extract.player_stats import extract_player_season_stats
from nba_pipeline.extract.shot_charts import extract_shot_charts
from nba_pipeline.extract.static import extract_players, extract_teams

app = typer.Typer(
    name="nba-pipeline",
    help="Refresh local NBA Parquet datasets via nba_api.",
    add_completion=False,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("nba_pipeline")


def _resolve_seasons(
    season: Optional[str],
    all_seasons: bool,
    cfg: Settings,
) -> list[str]:
    if season and all_seasons:
        raise typer.BadParameter("Use either --season or --all, not both.")
    if season:
        return [season]
    if all_seasons:
        return list(cfg.seasons)
    # Default: current (latest) season only for quick refresh.
    return [cfg.seasons[-1]]


@app.command()
def refresh(
    all_seasons: bool = typer.Option(
        False,
        "--all",
        help="Refresh all configured seasons (last 5).",
    ),
    season: Optional[str] = typer.Option(
        None,
        "--season",
        help="Single season label, e.g. 2023-24.",
    ),
    dataset: Optional[str] = typer.Option(
        None,
        "--dataset",
        help=f"One of: {', '.join(DATASETS)}. Default: all datasets.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Rebuild existing season partitions.",
    ),
    min_minutes: int = typer.Option(
        settings.min_minutes,
        "--min-minutes",
        help="Minutes filter for shot-chart player eligibility.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Fetch/report without writing curated data (static still lists sizes).",
    ),
    max_shot_players: Optional[int] = typer.Option(
        None,
        "--max-shot-players",
        help="Limit players per season for shot charts (testing).",
    ),
) -> None:
    """Extract → transform → load curated Parquet for selected datasets."""
    cfg = Settings(
        seasons=default_seasons(5),
        min_minutes=min_minutes,
    )
    cfg.ensure_dirs()

    if dataset is not None and dataset not in DATASETS:
        raise typer.BadParameter(f"Unknown dataset '{dataset}'. Choose from {DATASETS}.")

    seasons = _resolve_seasons(season, all_seasons, cfg)
    datasets = [dataset] if dataset else list(DATASETS)

    # Shot charts depend on player_season_stats for eligibility.
    if "shot_charts" in datasets and "player_season_stats" not in datasets:
        # Ensure stats exist for requested seasons; pull if missing.
        for s in seasons:
            stats_path = cfg.curated_dir / "player_season_stats" / f"season={s}" / "data.parquet"
            if not stats_path.exists():
                logger.info("player_season_stats missing for %s; extracting first", s)
                extract_player_season_stats([s], cfg=cfg, force=force, dry_run=dry_run)

    logger.info("Refresh datasets=%s seasons=%s force=%s dry_run=%s", datasets, seasons, force, dry_run)

    if "players" in datasets:
        extract_players(cfg=cfg, dry_run=dry_run)
    if "teams" in datasets:
        extract_teams(cfg=cfg, dry_run=dry_run)
    if "player_season_stats" in datasets:
        extract_player_season_stats(seasons, cfg=cfg, force=force, dry_run=dry_run)
    if "shot_charts" in datasets:
        extract_shot_charts(
            seasons,
            cfg=cfg,
            force=force,
            dry_run=dry_run,
            min_minutes=min_minutes,
            max_players=max_shot_players,
        )

    typer.echo("Refresh complete.")


@app.command("list-seasons")
def list_seasons() -> None:
    """Print the configured last-5 season labels."""
    for s in default_seasons(5):
        typer.echo(s)


if __name__ == "__main__":
    app()
