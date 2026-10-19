"""CLI for refreshing NBA Parquet datasets."""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import typer

from nba_pipeline.config import (
    DATASETS,
    GAMES_PER_SEASON,
    Settings,
    settings,
    warehouse_seasons,
)
from nba_pipeline.extract.player_stats import extract_player_season_stats
from nba_pipeline.extract.rosters import extract_rosters
from nba_pipeline.extract.shot_charts import extract_shot_charts
from nba_pipeline.extract.static import extract_players, extract_teams
from nba_pipeline.extract.team_stats import extract_team_season_stats
from nba_pipeline.modeling import team_wins, tracking
from nba_pipeline.modeling.team_wins import TrainedWinModel
from nba_pipeline.quality import check_warehouse, parse_since
from nba_pipeline.reporting import email_sender, weekly_win_report

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
        help="Refresh all configured seasons (5 training seasons + the target season).",
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
    cfg = Settings(min_minutes=min_minutes)
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
    if "team_season_stats" in datasets:
        extract_team_season_stats(seasons, cfg=cfg, force=force, dry_run=dry_run)
    if "rosters" in datasets:
        extract_rosters(seasons, cfg=cfg, dry_run=dry_run)
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


@app.command()
def check(
    season: Optional[str] = typer.Option(
        None,
        "--season",
        help="Season to inspect. Default: current (latest) season.",
    ),
    since: Optional[str] = typer.Option(
        None,
        "--since",
        help="ISO timestamp; require a successful refresh at or after it.",
    ),
) -> None:
    """Verify curated data is complete and free of obvious junk."""
    cfg = Settings()
    target = season or cfg.target_season

    try:
        cutoff = parse_since(since) if since else None
    except ValueError as exc:
        raise typer.BadParameter(f"Invalid --since timestamp: {exc}") from exc

    findings = check_warehouse(cfg=cfg, season=target, since=cutoff)
    if not findings:
        typer.echo(f"Data check passed for season {target}.")
        return

    typer.echo(f"Data check FAILED for season {target}: {len(findings)} problem(s).")
    for finding in findings:
        typer.echo(f"  - {finding.format()}")
    raise typer.Exit(code=1)


def _format_wins(value: float | None) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{value:.2f}"


def _echo_training_accuracy(model: TrainedWinModel) -> None:
    typer.echo(
        f"Trained on {len(model.training_seasons)} seasons: "
        f"{', '.join(model.training_seasons)}"
    )
    for metrics in (model.preseason_metrics, model.profile_metrics):
        typer.echo(
            f"  {metrics.name:<10} rows={metrics.rows:<4} "
            f"MAE={_format_wins(metrics.mae)} wins  "
            f"RMSE={_format_wins(metrics.rmse)}  "
            f"R2={_format_wins(metrics.r2)}"
        )
        if metrics.folds:
            folds = "  ".join(f"{s}:{v:.2f}" for s, v in sorted(metrics.folds.items()))
            typer.echo(f"             per-season MAE  {folds}")


def _echo_accuracy_report(predictions: pd.DataFrame, summary: dict) -> None:
    season = summary.get("season")
    if not summary.get("season_started"):
        typer.echo(
            f"{season} has not tipped off yet; showing the locked pre-season projection."
        )
        board = predictions.nlargest(30, "preseason_pred_wins")
        typer.echo(f"\n{'TEAM':<5} {'PROJ W':>7} {'PROJ L':>7}")
        for _, row in board.iterrows():
            wins = row["preseason_pred_wins"]
            typer.echo(
                f"{str(row.get('TEAM_ABBREVIATION') or ''):<5} "
                f"{wins:>7.1f} {GAMES_PER_SEASON - wins:>7.1f}"
            )
        return

    typer.echo(
        f"{season} through an average of {summary['games_played_avg']:.1f} games "
        f"({summary['season_progress_pct'] * 100:.1f}% of the season)"
    )
    typer.echo("\nAccuracy against wins banked so far:")
    typer.echo(
        f"  pre-season model   MAE={_format_wins(summary['mae_preseason_at_gp'])} wins  "
        f"RMSE={_format_wins(summary['rmse_preseason_at_gp'])}"
    )
    typer.echo(
        f"  in-season model    MAE={_format_wins(summary['mae_inseason_at_gp'])} wins  "
        f"RMSE={_format_wins(summary['rmse_inseason_at_gp'])}"
    )
    typer.echo(
        f"  82-game pace       pre-season MAE={_format_wins(summary['mae_pace_82_preseason'])}  "
        f"updated MAE={_format_wins(summary['mae_pace_82_updated'])}"
    )
    typer.echo(
        f"  standings rank     rho={_format_wins(summary['spearman_rho'])}  "
        f"playoff tier={_format_wins(summary['playoff_tier_accuracy'])}"
    )

    typer.echo(
        f"\n{'TEAM':<5} {'GP':>3} {'W':>3} {'L':>3} {'EXP W':>7} "
        f"{'ERR':>7} {'PACE':>6} {'PROJ':>6}"
    )
    for _, row in predictions.iterrows():
        typer.echo(
            f"{str(row.get('TEAM_ABBREVIATION') or ''):<5} "
            f"{row['games_played']:>3.0f} {row['actual_wins']:>3.0f} "
            f"{row['actual_losses']:>3.0f} "
            f"{row['preseason_expected_wins_at_gp']:>7.1f} "
            f"{row['preseason_win_error_at_gp']:>+7.1f} "
            f"{row['current_82_pace']:>6.1f} {row['updated_pred_wins']:>6.1f}"
        )


@app.command("train-wins")
def train_wins(
    target_season: Optional[str] = typer.Option(
        None,
        "--target-season",
        help="Season to project, e.g. 2026-27. Default: current season.",
    ),
    training_seasons: int = typer.Option(
        5,
        "--training-seasons",
        min=2,
        help="Completed seasons to train on.",
    ),
) -> None:
    """Fit the win models on completed seasons and project the target season."""
    cfg = Settings(training_season_count=training_seasons)
    season = target_season or cfg.target_season
    cfg = Settings(target_season=season, training_season_count=training_seasons)
    cfg.ensure_dirs()

    try:
        model, training, target = team_wins.train(
            season, cfg=cfg, n=training_seasons
        )
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc

    _echo_training_accuracy(model)

    predictions = team_wins.predict(model, training, target)
    summary = tracking.summarise(predictions, model)
    predictions_path = tracking.write_predictions(predictions, season, cfg=cfg)
    metrics_path = tracking.write_metrics(model, cfg=cfg)
    tracking.append_snapshot(predictions, summary, season, cfg=cfg)

    typer.echo("")
    _echo_accuracy_report(predictions, summary)
    typer.echo(f"\nWrote {predictions_path}")
    typer.echo(f"Wrote {metrics_path}")


@app.command("check-wins")
def check_wins(
    season: Optional[str] = typer.Option(
        None,
        "--season",
        help="Season to evaluate. Default: current season.",
    ),
    offline: bool = typer.Option(
        False,
        "--offline",
        help="Skip the API refresh and read curated Parquet only.",
    ),
    training_seasons: int = typer.Option(
        5,
        "--training-seasons",
        min=2,
        help="Completed seasons to train on.",
    ),
) -> None:
    """Re-score the win models against the games played so far."""
    base = Settings()
    target = season or base.target_season
    cfg = Settings(target_season=target, training_season_count=training_seasons)
    cfg.ensure_dirs()

    if not offline:
        logger.info("Refreshing team_season_stats for %s", target)
        extract_team_season_stats([target], cfg=cfg, force=True)

    try:
        model, training, target_frame = team_wins.train(
            target, cfg=cfg, n=training_seasons
        )
        predictions = team_wins.predict(model, training, target_frame)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    # Keep the locked pre-season projection from the original training run so
    # in-season accuracy is measured against what was actually forecast.
    predictions = _restore_locked_preseason(predictions, target, cfg)

    summary = tracking.summarise(predictions, model)
    tracking.write_predictions(predictions, target, cfg=cfg)
    history_path = tracking.append_snapshot(predictions, summary, target, cfg=cfg)

    _echo_accuracy_report(predictions, summary)

    history = tracking.load_history(target, cfg=cfg)
    deltas = tracking.week_over_week(history, summary["as_of_date"])
    if deltas:
        typer.echo(f"\nVersus {deltas['compared_to']}:")
        for key in ("mae_preseason_at_gp", "mae_inseason_at_gp", "spearman_rho"):
            if key in deltas:
                typer.echo(f"  {key:<22} {deltas[key]:+.3f}")

    typer.echo(f"\nSnapshot appended to {history_path}")


def _restore_locked_preseason(
    predictions: pd.DataFrame,
    season: str,
    cfg: Settings,
) -> pd.DataFrame:
    """Carry forward the stored pre-season projection, if one was saved.

    A pre-season forecast is only a forecast if it stops moving once the season
    starts, so the number written by `train-wins` is authoritative.
    """
    try:
        stored = tracking.load_predictions(season, cfg=cfg)
    except FileNotFoundError:
        logger.info("No stored predictions for %s; using a freshly fit baseline", season)
        return predictions
    if "preseason_pred_wins" not in stored.columns:
        return predictions

    locked = dict(
        zip(stored["TEAM_ID"].astype("int64"), stored["preseason_pred_wins"])
    )
    out = predictions.copy()
    out["preseason_pred_wins"] = [
        locked.get(int(team), value)
        for team, value in zip(out["TEAM_ID"], out["preseason_pred_wins"])
    ]
    return team_wins.rescore_with_preseason(out)


@app.command("send-win-report")
def send_win_report(
    season: Optional[str] = typer.Option(
        None,
        "--season",
        help="Season to report on. Default: current season.",
    ),
    to: Optional[str] = typer.Option(
        None,
        "--to",
        help="Recipient address. Default: the configured report recipient.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Render and archive the report without sending it.",
    ),
) -> None:
    """Email the weekly digest of how the win predictor is performing."""
    base = Settings()
    target = season or base.target_season
    cfg = Settings(target_season=target)
    cfg.ensure_dirs()

    try:
        predictions = tracking.load_predictions(target, cfg=cfg)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc

    history = tracking.load_history(target, cfg=cfg)
    summary = tracking.summarise(predictions)
    stored = tracking.load_metrics(cfg=cfg) or {}
    # Training accuracy lives with the model, not the per-run snapshot.
    summary.setdefault("training_seasons", stored.get("training_seasons"))
    for key, source in (
        ("cv_mae_preseason", "preseason_model"),
        ("cv_rmse_preseason", "preseason_model"),
        ("cv_r2_preseason", "preseason_model"),
        ("cv_mae_profile", "profile_model"),
        ("cv_rmse_profile", "profile_model"),
        ("cv_r2_profile", "profile_model"),
    ):
        metric = key.rsplit("_", 1)[0].replace("cv_", "")
        summary.setdefault(key, (stored.get(source) or {}).get(metric))

    report = weekly_win_report.build(
        predictions, summary, history=history, metrics=stored, cfg=cfg
    )
    archived = report.archive(cfg)

    try:
        outcome = email_sender.send_report(
            subject=report.subject,
            text_body=report.text_body,
            html_body=report.html_body,
            to=to,
            cfg=cfg,
            dry_run=dry_run,
        )
    except email_sender.EmailNotConfigured as exc:
        typer.echo(f"Report archived to {archived}")
        typer.echo(f"Email not sent: {exc}")
        raise typer.Exit(code=1) from exc

    typer.echo(report.subject)
    typer.echo(f"Report archived to {archived}")
    typer.echo(outcome)


@app.command("list-seasons")
def list_seasons() -> None:
    """Print the warehouse seasons, marking the target season."""
    cfg = Settings()
    for s in warehouse_seasons(cfg.target_season, cfg.training_season_count):
        typer.echo(f"{s} (target)" if s == cfg.target_season else s)


if __name__ == "__main__":
    app()
