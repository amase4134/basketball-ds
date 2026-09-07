import { fmtCount, fmtDate, fmtNumber, fmtPercent } from './format';

export type FilterKind = 'range' | 'text' | 'enum' | null;

export interface ColumnDef {
  key: string;
  label: string;
  /** Column headers only offer sorting for keys the API allow-lists. */
  sortable?: boolean;
  filter?: FilterKind;
  options?: { value: string; label: string }[];
  /** Essential columns stay visible so a row always identifies itself. */
  essential?: boolean;
  numeric?: boolean;
  hint?: string;
  render?: (value: unknown) => string;
}

const rate = (key: string, label: string, hint = 'Per 100 possessions'): ColumnDef =>
  ({ key, label, sortable: true, filter: 'range', numeric: true, hint, render: v => fmtNumber(v, 1) });

const pct = (key: string, label: string): ColumnDef =>
  ({ key, label, sortable: true, filter: 'range', numeric: true, hint: 'Share between 0 and 1', render: fmtPercent });

export const PLAYER_COLUMNS: ColumnDef[] = [
  { key: 'PLAYER_NAME', label: 'Player', sortable: true, filter: 'text', essential: true, render: String },
  { key: 'TEAM_ABBREVIATION', label: 'Team', sortable: true, filter: 'text', essential: true, render: String },
  { key: 'AGE', label: 'Age', sortable: true, filter: 'range', numeric: true, render: v => fmtNumber(v, 0) },
  { key: 'GP', label: 'GP', sortable: true, filter: 'range', numeric: true, hint: 'Games played', render: fmtCount },
  { key: 'MIN', label: 'MIN', sortable: true, filter: 'range', numeric: true, hint: 'Season total minutes', render: fmtCount },
  rate('PTS', 'PTS'),
  rate('REB', 'REB'),
  rate('AST', 'AST'),
  { key: 'FGM', label: 'FGM', sortable: true, filter: 'range', numeric: true, hint: 'Per 100 possessions', render: v => fmtNumber(v, 1) },
  rate('FGA', 'FGA'),
  pct('FG_PCT', 'FG%'),
  rate('FG3A', '3PA'),
  pct('FG3_PCT', '3P%'),
  rate('TOV', 'TOV'),
  rate('STL', 'STL'),
  rate('BLK', 'BLK'),
  { key: 'PLUS_MINUS', label: '+/−', sortable: true, filter: 'range', numeric: true, render: v => fmtNumber(v, 1) },
  { key: 'season', label: 'Season', render: String },
];

export const SHOT_COLUMNS: ColumnDef[] = [
  { key: 'GAME_DATE', label: 'Game date', sortable: true, essential: true, render: fmtDate },
  { key: 'PLAYER_NAME', label: 'Player', sortable: true, essential: true, render: String },
  { key: 'EVENT_TYPE', label: 'Event', sortable: true, render: String },
  {
    key: 'SHOT_MADE_FLAG',
    label: 'Result',
    sortable: true,
    filter: 'enum',
    options: [{ value: 'made', label: 'Made only' }, { value: 'missed', label: 'Missed only' }],
    render: v => (v ? 'Made ✓' : 'Miss ✕'),
  },
  {
    key: 'SHOT_TYPE',
    label: 'Type',
    sortable: true,
    filter: 'enum',
    options: [{ value: '2PT Field Goal', label: '2PT only' }, { value: '3PT Field Goal', label: '3PT only' }],
    render: String,
  },
  { key: 'SHOT_DISTANCE', label: 'Distance (ft)', sortable: true, filter: 'range', numeric: true, render: v => fmtNumber(v, 0) },
  { key: 'LOC_X', label: 'LOC X', sortable: true, filter: 'range', numeric: true, render: v => fmtNumber(v, 0) },
  { key: 'LOC_Y', label: 'LOC Y', sortable: true, filter: 'range', numeric: true, render: v => fmtNumber(v, 0) },
];

/** Columns hidden on first load to keep the default table readable. */
export const PLAYER_DEFAULT_HIDDEN = ['FGM', 'AGE', 'season'];
export const SHOT_DEFAULT_HIDDEN = ['LOC_X', 'LOC_Y', 'EVENT_TYPE'];

export function renderCell(column: ColumnDef, value: unknown): string {
  if (value === null || value === undefined) return '—';
  return column.render ? column.render(value) : String(value);
}
