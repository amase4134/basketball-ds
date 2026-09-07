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
