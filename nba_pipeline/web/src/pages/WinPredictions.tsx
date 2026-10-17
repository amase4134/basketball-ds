import { Link, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api, queryString } from '../api/client';
import type { SortDirection, SurpriseTeam, WinPredictionHistory, WinPredictionRow, WinPredictions as WinPredictionsResponse } from '../api/types';
import { AccuracyTrend, TeamTrajectory } from '../components/AccuracyTrend';
import { AsyncState } from '../components/AsyncState';
import { StatCards } from '../components/StatCards';
import { WinProjectionChart } from '../components/WinProjectionChart';
import { fmtNumber, fmtPercent } from '../lib/format';

type ColumnKey = keyof WinPredictionRow | 'record';

interface Column {
  key: ColumnKey;
  sortKey?: string;
  label: string;
  hint: string;
  digits?: number;
  numeric?: boolean;
  signed?: boolean;
  inSeasonOnly?: boolean;
}

const COLUMNS: Column[] = [
  { key: 'TEAM_ABBREVIATION', label: 'Team', hint: 'Team' },
  { key: 'games_played', label: 'GP', hint: 'Games played', digits: 0, numeric: true, inSeasonOnly: true },
  { key: 'record', sortKey: 'actual_wins', label: 'W-L', hint: 'Actual record', numeric: true, inSeasonOnly: true },
  { key: 'preseason_expected_wins_at_gp', label: 'Exp W', hint: 'Wins the pre-season model expected by this point', digits: 1, numeric: true, inSeasonOnly: true },
  { key: 'preseason_win_error_at_gp', label: 'Error', hint: 'Actual wins minus expected wins', digits: 1, numeric: true, signed: true, inSeasonOnly: true },
  { key: 'preseason_pred_wins', label: 'Pre 82', hint: 'Locked pre-season 82-game projection', digits: 1, numeric: true },
  { key: 'inseason_talent_wins_82', label: 'In 82', hint: '82-game projection from current efficiency', digits: 1, numeric: true, inSeasonOnly: true },
  { key: 'updated_pred_wins', label: 'Proj 82', hint: 'Banked wins plus the projected remainder', digits: 1, numeric: true },
  { key: 'current_82_pace', label: 'Pace', hint: 'Current win pace over 82 games', digits: 1, numeric: true, inSeasonOnly: true },
  { key: 'net_rating', label: 'Net', hint: 'Net rating per 100 possessions', digits: 1, numeric: true, signed: true, inSeasonOnly: true },
];

const ARROW = { asc: '↑', desc: '↓' } as const;

export function WinPredictions() {
  const [params, setParams] = useSearchParams();
  const season = params.get('season') ?? '';
  const sort = params.get('sort') ?? 'updated_pred_wins';
  const direction = (params.get('direction') === 'asc' ? 'asc' : 'desc') as SortDirection;
  const teamParam = params.get('team') ?? '';

  const update = (changes: Record<string, string | undefined>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setParams(next, { replace: true });
  };

  const predictions = useQuery({
    queryKey: ['win-predictions', season, sort, direction],
    queryFn: () => api<WinPredictionsResponse>(`/predictions/wins?${queryString({ season, sort, direction })}`),
  });

  const resolvedSeason = predictions.data?.meta.season ?? season;
  const history = useQuery({
    queryKey: ['win-prediction-history', resolvedSeason, teamParam],
    queryFn: () =>
      api<WinPredictionHistory>(`/predictions/wins/history?${queryString({ season: resolvedSeason, team_id: teamParam })}`),
    enabled: Boolean(resolvedSeason),
    // A missing history file is an expected state before the first snapshot.
    retry: false,
  });

  return (
    <>
      <h1>Team win predictor</h1>
      <p className="lede">
        Two models project every team's 82-game win total: one locked in before the season from the
        previous year's efficiency, and one that re-reads each team's efficiency as games are played.
        Accuracy is scored against the wins actually banked and recorded every day.
      </p>

      <AsyncState query={predictions} label="the win predictions">
        {data => {
          const { summary, meta, items } = data;
          const started = summary.season_started;
          const columns = COLUMNS.filter(column => started || !column.inSeasonOnly);
          const selectedTeam = items.find(row => String(row.TEAM_ID) === teamParam);

          return (
            <>
              <div className="filters">
                {meta.seasons.length > 1 && (
                  <label>
                    Season
                    <select value={meta.season} onChange={event => update({ season: event.target.value, team: undefined })}>
                      {meta.seasons.map(item => <option key={item} value={item}>{item}</option>)}
                    </select>
                  </label>
                )}
                <label>
                  Team trajectory
                  <select value={teamParam} onChange={event => update({ team: event.target.value || undefined })}>
                    <option value="">League-wide accuracy</option>
                    {[...items]
                      .sort((a, b) => (a.TEAM_ABBREVIATION ?? '').localeCompare(b.TEAM_ABBREVIATION ?? ''))
                      .map(row => (
                        <option key={row.TEAM_ID} value={String(row.TEAM_ID)}>
                          {row.TEAM_NAME ?? row.TEAM_ABBREVIATION ?? row.TEAM_ID}
                        </option>
                      ))}
                  </select>
                </label>
              </div>

              <StatCards cards={summaryCards(data)} />

              <h2>{started ? 'Projection versus reality' : 'Pre-season projection'}</h2>
              <WinProjectionChart rows={items} started={started} />

              <h2>{selectedTeam ? `${selectedTeam.TEAM_NAME ?? selectedTeam.TEAM_ABBREVIATION} trajectory` : 'Accuracy over the season'}</h2>
              <AsyncState query={history} label="the accuracy history">
                {record =>
                  selectedTeam && record.team
                    ? <TeamTrajectory points={record.team.items} team={selectedTeam.TEAM_ABBREVIATION ?? String(selectedTeam.TEAM_ID)} />
                    : <AccuracyTrend points={record.items} />
                }
              </AsyncState>

              {started && (
                <>
                  <h2>Biggest surprises</h2>
                  <div className="surprise-grid">
                    <SurpriseList title="Overperforming the pre-season model" rows={summary.overperformers} tone="positive" />
                    <SurpriseList title="Underperforming the pre-season model" rows={summary.underperformers} tone="negative" />
                  </div>
                </>
              )}

              <h2>All {items.length} teams</h2>
              <div className="table-wrap">
                <table>
                  <caption className="visually-hidden">
                    Win projections and accuracy for every team in {meta.season}
                  </caption>
                  <thead>
                    <tr>
                      {columns.map(column => {
                        const key = column.sortKey ?? String(column.key);
                        const active = sort === key;
                        return (
                          <th
                            key={String(column.key)}
                            scope="col"
                            className={column.numeric ? 'numeric' : undefined}
                            aria-sort={active ? (direction === 'asc' ? 'ascending' : 'descending') : 'none'}
                          >
                            <div className="th-content">
                              <button
                                type="button"
                                className="sort-toggle"
                                title={column.hint}
                                onClick={() => update({ sort: key, direction: active && direction === 'desc' ? 'asc' : 'desc' })}
                              >
                                <span>{column.label}</span>
                                <span className="sort-arrow" aria-hidden="true">{active ? ARROW[direction] : '↕'}</span>
                                <span className="visually-hidden">
                                  {active ? `sorted ${direction === 'asc' ? 'ascending' : 'descending'}, activate to reverse` : 'activate to sort'}
                                </span>
                              </button>
                            </div>
                          </th>
                        );
                      })}
                    </tr>
                  </thead>
                  <tbody>
                    {items.map(row => (
                      <tr key={row.TEAM_ID} className={String(row.TEAM_ID) === teamParam ? 'is-selected' : undefined}>
                        {columns.map(column => (
                          <td key={String(column.key)} className={cellClass(column, row)}>
                            {cellValue(column, row)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <p className="hint">
                Projections are reconciled so the league totals{' '}
                {fmtNumber((meta.games_per_season * summary.teams) / 2, 0)} wins, since every win is also
                a loss. Trained on {meta.training_seasons.join(', ')}.
              </p>

              <p className="selection-actions">
                <Link className="button" to={`/players?season=${meta.season}`}>Explore players</Link>
                <Link className="button" to="/">Back to dashboard</Link>
              </p>
            </>
          );
        }}
      </AsyncState>
    </>
  );
}

function summaryCards(data: WinPredictionsResponse) {
  const { summary, meta } = data;
  const { training } = summary;
  const started = summary.season_started;
  const seasons = meta.training_seasons;

  return [
    {
      label: 'Training window',
      value: `${seasons.length} seasons`,
      note: seasons.length > 0 ? `${seasons[0]} – ${seasons[seasons.length - 1]}` : 'not yet trained',
    },
    {
      label: 'Season progress',
      value: `${fmtNumber(summary.games_played_avg, 1)} / ${meta.games_per_season}`,
      note: started ? `${fmtPercent(summary.season_progress_pct)} played` : 'not tipped off yet',
    },
    {
      label: 'Pre-season MAE',
      value: started ? `${fmtNumber(summary.mae_preseason_at_gp, 2)} wins` : '—',
      note: started ? 'versus wins banked' : 'awaiting first games',
    },
    {
      label: 'In-season MAE',
      value: started ? `${fmtNumber(summary.mae_inseason_at_gp, 2)} wins` : '—',
      note: started ? 'versus wins banked' : 'awaiting first games',
    },
    {
      label: 'Rank correlation',
      value: fmtNumber(summary.spearman_rho, 3),
      note: started ? 'projected vs actual order' : 'awaiting first games',
    },
    {
      label: 'Cross-validated MAE',
      value: `${fmtNumber(training.profile_model.mae, 2)} wins`,
      note: `in-season model, R² ${fmtNumber(training.profile_model.r2, 2)}`,
    },
  ];
}

function SurpriseList({ title, rows, tone }: { title: string; rows: SurpriseTeam[]; tone: 'positive' | 'negative' }) {
  if (rows.length === 0) return <p className="notice notice-quiet">{title}: nothing to report yet.</p>;
  return (
    <section className="surprise-card">
      <h3>{title}</h3>
      <ol>
        {rows.map(row => (
          <li key={row.TEAM_ID}>
            <strong>{row.TEAM_ABBREVIATION ?? row.TEAM_NAME}</strong>
            <span className="surprise-record">{fmtNumber(row.actual_wins, 0)}-{fmtNumber(row.actual_losses, 0)}</span>
            <span className={`surprise-delta is-${tone}`}>
              {signed(row.preseason_win_error_at_gp, 1)} wins
            </span>
            <small>
              expected {fmtNumber(row.preseason_expected_wins_at_gp, 1)}, now projecting{' '}
              {fmtNumber(row.updated_pred_wins, 1)}
            </small>
          </li>
        ))}
      </ol>
    </section>
  );
}

function signed(value: number | null | undefined, digits: number): string {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  return `${n > 0 ? '+' : ''}${fmtNumber(n, digits)}`;
}

function cellValue(column: Column, row: WinPredictionRow): string {
  if (column.key === 'record') return `${fmtNumber(row.actual_wins, 0)}-${fmtNumber(row.actual_losses, 0)}`;
  if (column.key === 'TEAM_ABBREVIATION') return row.TEAM_ABBREVIATION ?? String(row.TEAM_ID);
  const value = row[column.key as keyof WinPredictionRow];
  if (column.signed) return signed(value as number | null, column.digits ?? 1);
  return fmtNumber(value, column.digits);
}

function cellClass(column: Column, row: WinPredictionRow): string | undefined {
  const classes = column.numeric ? ['numeric'] : [];
  if (column.key === 'preseason_win_error_at_gp') {
    const value = Number(row.preseason_win_error_at_gp);
    // Only call out a gap that has grown past a rounding artefact.
    if (Number.isFinite(value) && Math.abs(value) >= 0.05) {
      classes.push(value > 0 ? 'is-above' : 'is-below');
    }
  }
  return classes.length > 0 ? classes.join(' ') : undefined;
}
