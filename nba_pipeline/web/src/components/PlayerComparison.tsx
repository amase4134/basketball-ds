import { useQuery } from '@tanstack/react-query';
import { api, queryString } from '../api/client';
import type { Comparison, Row } from '../api/types';
import { AsyncState } from './AsyncState';
import { PLAYER_COLORS } from './Court';
import { PLAYER_COLUMNS, renderCell } from '../lib/columns';

interface PlayerComparisonProps {
  season: string;
  players: number[];
}

const columnFor = (key: string) => PLAYER_COLUMNS.find(column => column.key === key);

/** Side-by-side metrics for the current multi-player selection, with the leader marked. */
export function PlayerComparison({ season, players }: PlayerComparisonProps) {
  const enabled = Boolean(season) && players.length >= 2;
  const comparison = useQuery({
    queryKey: ['compare', season, players.join(',')],
    queryFn: () => api<Comparison>(`/players/compare?${queryString({ season, player_ids: players.join(',') })}`),
    enabled,
  });

  if (players.length < 2) {
    return (
      <p className="notice notice-quiet">
        Select two or more players to compare them side by side.
      </p>
    );
  }

  return (
    <section className="comparison">
      <h2>Selected player comparison</h2>
      <AsyncState query={comparison} label="the comparison">
        {data => {
          const isLeader = (metric: string, row: Row) =>
            data.leaders[metric]?.PLAYER_ID === Number(row.PLAYER_ID) && data.leaders[metric]?.TEAM_ID === Number(row.TEAM_ID);
          return (
            <div className="table-wrap">
              <table className="comparison-table">
                <caption className="visually-hidden">
                  Metric comparison for {data.meta.returned} selected player rows in {season}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Metric</th>
                    {data.items.map(row => (
                      <th key={`${row.PLAYER_ID}-${row.TEAM_ID}`} scope="col">
                        <span className="legend-swatch" style={{ background: PLAYER_COLORS[players.indexOf(Number(row.PLAYER_ID)) % PLAYER_COLORS.length] }} aria-hidden="true" />
                        {String(row.PLAYER_NAME)}
                        <small>{String(row.TEAM_ABBREVIATION)}</small>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.metrics.map(metric => {
                    const column = columnFor(metric);
                    const leader = data.leaders[metric];
                    return (
                      <tr key={metric}>
                        <th scope="row">
                          {column?.label ?? metric}
                          {leader?.lower_is_better && <small> (lower is better)</small>}
                        </th>
                        {data.items.map(row => (
                          <td key={`${row.PLAYER_ID}-${row.TEAM_ID}`} className={isLeader(metric, row) ? 'numeric is-leader' : 'numeric'}>
                            {column ? renderCell(column, row[metric]) : String(row[metric] ?? '—')}
                            {isLeader(metric, row) && <span className="visually-hidden"> (best of selection)</span>}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          );
        }}
      </AsyncState>
    </section>
  );
}
