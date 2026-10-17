export type Row = Record<string, unknown>;

export interface PageMeta {
  page: number;
  page_size: number;
  total: number;
  sort?: string;
  direction?: SortDirection;
  filters?: Record<string, unknown>;
}

export interface Paged<T = Row> {
  items: T[];
  meta: PageMeta;
}

export type SortDirection = 'asc' | 'desc';

export interface Catalog {
  seasons: string[];
  datasets: Record<string, { available: boolean; rows: number }>;
  grid: CourtGrid;
  limits?: { max_selected_players: number; page_sizes: number[] };
  shot_coverage: string;
  refresh_log?: Row[];
  target_season?: string;
  training_seasons?: string[];
}

export interface CourtGrid {
  x_min: number;
  x_max: number;
  y_min: number;
  y_max: number;
  cell_size: number;
  point_limit: number;
}

export interface PlayerOption {
  PLAYER_ID: number;
  FULL_NAME: string;
}

export interface ShotSummary {
  fga: number;
  fgm: number;
  fg_pct: number | null;
  two_pa: number;
  two_pct: number | null;
  three_pa: number;
  three_pct: number | null;
  avg_distance: number | null;
  selected_player_count: number;
  total_matching: number;
  recommended_mode: 'points' | 'bins';
}

export interface ShotMap {
  mode: 'points' | 'bins';
  metric?: 'attempts' | 'makes' | 'fg_pct';
  items: Row[];
  total_matching: number;
  returned: number;
  truncated: boolean;
  grid: CourtGrid;
  player_ids: number[];
}

export interface ComparisonLeader {
  PLAYER_ID: number;
  TEAM_ID: number;
  value: number;
  lower_is_better: boolean;
}

export interface Comparison {
  items: Row[];
  leaders: Record<string, ComparisonLeader>;
  metrics: string[];
  meta: { season: string; requested: number[]; returned: number };
}

/** One team's projection and its accuracy so far in the tracked season. */
export interface WinPredictionRow {
  TEAM_ID: number;
  TEAM_NAME: string | null;
  TEAM_ABBREVIATION: string | null;
  games_played: number;
  actual_wins: number;
  actual_losses: number;
  actual_win_pct: number | null;
  current_82_pace: number | null;
  net_rating: number | null;
  preseason_pred_wins: number;
  inseason_talent_wins_82: number;
  preseason_expected_wins_at_gp: number | null;
  inseason_expected_wins_at_gp: number | null;
  preseason_win_error_at_gp: number | null;
  inseason_win_error_at_gp: number | null;
  profile_trust: number;
  updated_pred_wins: number;
  preseason_rank: number | null;
  updated_rank: number | null;
  actual_rank: number | null;
  season: string;
}

export interface ModelAccuracy {
  rows: number | null;
  mae: number | null;
  rmse: number | null;
  r2: number | null;
  alpha: number | null;
}

export interface TrainingAccuracy {
  trained_at: string | null;
  training_seasons: string[];
  preseason_model: ModelAccuracy;
  profile_model: ModelAccuracy;
}

/** A team that is beating or trailing its pre-season expectation. */
export interface SurpriseTeam {
  TEAM_ID: number;
  TEAM_ABBREVIATION: string | null;
  TEAM_NAME: string | null;
  games_played: number;
  actual_wins: number;
  actual_losses: number;
  preseason_pred_wins: number;
  preseason_expected_wins_at_gp: number | null;
  preseason_win_error_at_gp: number | null;
  updated_pred_wins: number;
  current_82_pace: number | null;
}

export interface WinPredictionSummary {
  season: string | null;
  as_of_date: string;
  teams: number;
  season_started: boolean;
  games_played_avg: number;
  games_played_max: number;
  season_progress_pct: number;
  total_games_played: number;
  mae_preseason_at_gp: number | null;
  rmse_preseason_at_gp: number | null;
  mae_inseason_at_gp: number | null;
  rmse_inseason_at_gp: number | null;
  mae_pace_82_preseason: number | null;
  rmse_pace_82_preseason: number | null;
  mae_pace_82_updated: number | null;
  spearman_rho: number | null;
  spearman_rho_preseason: number | null;
  playoff_tier_accuracy: number | null;
  play_in_tier_accuracy: number | null;
  overperformers: SurpriseTeam[];
  underperformers: SurpriseTeam[];
  training: TrainingAccuracy;
}

export interface WinPredictions {
  items: WinPredictionRow[];
  summary: WinPredictionSummary;
  meta: {
    season: string;
    seasons: string[];
    sort: string;
    direction: SortDirection;
    games_per_season: number;
    training_seasons: string[];
  };
}

/** League accuracy at one snapshot date. */
export interface AccuracySnapshot {
  as_of_date: string;
  games_played_avg: number | null;
  season_progress_pct: number | null;
  mae_preseason_at_gp: number | null;
  rmse_preseason_at_gp: number | null;
  mae_inseason_at_gp: number | null;
  rmse_inseason_at_gp: number | null;
  mae_pace_82_preseason: number | null;
  mae_pace_82_updated: number | null;
  spearman_rho: number | null;
  spearman_rho_preseason: number | null;
  playoff_tier_accuracy: number | null;
  play_in_tier_accuracy: number | null;
}

export interface TeamTrajectoryPoint {
  as_of_date: string;
  games_played: number | null;
  actual_wins: number | null;
  actual_losses: number | null;
  preseason_expected_wins_at_gp: number | null;
  inseason_expected_wins_at_gp: number | null;
  preseason_win_error_at_gp: number | null;
  preseason_pred_wins: number | null;
  updated_pred_wins: number | null;
  current_82_pace: number | null;
}

export interface WinPredictionHistory {
  items: AccuracySnapshot[];
  meta: { season: string; snapshots: number; games_per_season: number };
  team?: { TEAM_ID: number; items: TeamTrajectoryPoint[] };
}
