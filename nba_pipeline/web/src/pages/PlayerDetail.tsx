import { useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api, queryString } from '../api/client';
import type { Row } from '../api/types';
import { AsyncState } from '../components/AsyncState';
import { StatCards } from '../components/StatCards';
import { PLAYER_COLUMNS, renderCell } from '../lib/columns';
import { fmtCount, fmtNumber, fmtPercent } from '../lib/format';

interface PlayerDetailResponse {
  player: Row;
  season_rows: Row[];
}

interface ComparisonResponse {
  selected: Row;
  compare_to: Row;
  deltas: Record<string, number | null>;
}

const SUMMARY_KEYS = ['PTS', 'REB', 'AST', 'MIN', 'GP', 'FG_PCT'];
const DETAIL_COLUMNS = PLAYER_COLUMNS.filter(column => column.key !== 'PLAYER_NAME');

export function PlayerDetail() {
  const { playerId = '' } = useParams();
  const [params] = useSearchParams();
  const season = params.get('season') ?? '';
  const [compareTo, setCompareTo] = useState('');

  const detail = useQuery({
    queryKey: ['player', playerId],
    queryFn: () => api<PlayerDetailResponse>(`/players/${playerId}`),
  });

  const comparison = useQuery({
    queryKey: ['player-seasons', playerId, season, compareTo],
    queryFn: () => api<ComparisonResponse>(`/players/${playerId}/comparison?${queryString({ season, compare_to: compareTo })}`),
    enabled: Boolean(season && compareTo && compareTo !== season),
  });

  return (
    <AsyncState query={detail} label="the player">
      {data => {
        const rows = data.season_rows;
        const selected = rows.find(row => row.season === season) ?? rows[0];
        const seasons = [...new Set(rows.map(row => String(row.season)))];
        const activeSeason = String(selected?.season ?? season);
        return (
          <>
            <h1>{String(data.player.FULL_NAME ?? selected?.PLAYER_NAME ?? 'Player')}</h1>
            <p className="lede">
              Source team rows are shown without a combined total. Rates are per 100 possessions;
              <abbr title="Minutes"> MIN</abbr> and <abbr title="Games played">GP</abbr> are season totals.
            </p>

            {selected && (
              <StatCards
                cards={SUMMARY_KEYS.map(key => {
                  const column = PLAYER_COLUMNS.find(item => item.key === key);
                  return {
                    label: `${column?.label ?? key} · ${activeSeason}`,
                    value: column ? renderCell(column, selected[key]) : String(selected[key] ?? '—'),
                    note: column?.hint,
                  };
                })}
              />
            )}

            <h2>Season trend</h2>
            <SeasonTrend rows={[...rows].reverse()} />

            <h2>Available seasons and teams</h2>
            <div className="table-wrap">
              <table>
                <caption className="visually-hidden">All available season and team rows for this player</caption>
                <thead>
                  <tr>{DETAIL_COLUMNS.map(column => <th key={column.key} scope="col" className={column.numeric ? 'numeric' : undefined}>{column.label}</th>)}</tr>
                </thead>
                <tbody>
                  {rows.map(row => (
                    <tr key={`${row.season}-${row.TEAM_ID}`} className={row.season === activeSeason ? 'is-selected' : undefined}>
                      {DETAIL_COLUMNS.map(column => (
                        <td key={column.key} className={column.numeric ? 'numeric' : undefined}>{renderCell(column, row[column.key])}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <h2>Compare two seasons</h2>
            <div className="filters">
              <label>
                Compare {activeSeason} with
                <select value={compareTo} onChange={event => setCompareTo(event.target.value)}>
                  <option value="">Select a season</option>
                  {seasons.filter(item => item !== activeSeason).map(item => <option key={item} value={item}>{item}</option>)}
                </select>
              </label>
            </div>
            {compareTo && (
              <AsyncState query={comparison} label="the season comparison">
                {result => (
                  <div className="table-wrap">
                    <table>
                      <caption className="visually-hidden">Metric deltas between {activeSeason} and {compareTo}</caption>
                      <thead>
                        <tr>
                          <th scope="col">Metric</th>
                          <th scope="col" className="numeric">{activeSeason}</th>
                          <th scope="col" className="numeric">{compareTo}</th>
                          <th scope="col" className="numeric">Change</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(result.deltas).map(([metric, delta]) => {
                          const column = PLAYER_COLUMNS.find(item => item.key === metric);
                          const isPercent = metric.includes('PCT');
                          return (
                            <tr key={metric}>
                              <th scope="row">{column?.label ?? metric}</th>
                              <td className="numeric">{column ? renderCell(column, result.selected[metric]) : '—'}</td>
                              <td className="numeric">{column ? renderCell(column, result.compare_to[metric]) : '—'}</td>
                              <td className="numeric">
                                {delta === null || delta === undefined
                                  ? '—'
                                  : `${delta > 0 ? '+' : ''}${isPercent ? fmtPercent(delta) : fmtNumber(delta, 1)}`}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </AsyncState>
            )}

            <p className="selection-actions">
              <Link className="button" to={`/shots?season=${activeSeason}&players=${playerId}`}>Open shot explorer</Link>
              <Link className="button" to={`/players?season=${activeSeason}&players=${playerId}`}>Back to player table</Link>
            </p>
          </>
        );
      }}
    </AsyncState>
  );
}

const TREND_METRICS = [
  { key: 'PTS', label: 'PTS /100', color: 'var(--accent)' },
  { key: 'REB', label: 'REB /100', color: 'var(--accent-alt)' },
  { key: 'AST', label: 'AST /100', color: 'var(--positive)' },
];

/** Minimal inline SVG trend so the app keeps its no-extra-dependency chart approach. */
function SeasonTrend({ rows }: { rows: Row[] }) {
  const seasons = [...new Set(rows.map(row => String(row.season)))];
  if (seasons.length < 2) return <p className="notice notice-quiet">A trend needs at least two seasons of data.</p>;

  // Traded seasons hold several rows; chart the row with the most minutes for each season.
  const points = seasons.map(season => {
    const candidates = rows.filter(row => String(row.season) === season);
    return candidates.reduce((best, row) => (Number(row.MIN ?? 0) > Number(best.MIN ?? 0) ? row : best), candidates[0]);
  });
  const peak = Math.max(1, ...points.flatMap(row => TREND_METRICS.map(metric => Number(row[metric.key] ?? 0))));
  const x = (index: number) => 40 + (index * 420) / Math.max(1, points.length - 1);
  const y = (value: number) => 170 - (value / peak) * 140;

  return (
    <figure className="trend">
      <svg viewBox="0 0 480 200" role="img" aria-label={`Per-100-possession trend across ${points.length} seasons`}>
        <line x1="40" y1="170" x2="470" y2="170" className="axis" />
        <line x1="40" y1="20" x2="40" y2="170" className="axis" />
        {TREND_METRICS.map(metric => (
          <polyline
            key={metric.key}
            fill="none"
            stroke={metric.color}
            strokeWidth="2"
            points={points.map((row, index) => `${x(index)},${y(Number(row[metric.key] ?? 0))}`).join(' ')}
          />
        ))}
        {TREND_METRICS.map(metric =>
          points.map((row, index) => (
            <circle key={`${metric.key}-${index}`} cx={x(index)} cy={y(Number(row[metric.key] ?? 0))} r="3" fill={metric.color}>
              <title>{`${String(row.season)} ${metric.label}: ${fmtNumber(row[metric.key], 1)}`}</title>
            </circle>
          )),
        )}
        {points.map((row, index) => (
          <text key={String(row.season)} x={x(index)} y="188" textAnchor="middle" className="axis-label">{String(row.season)}</text>
        ))}
        <text x="36" y="24" textAnchor="end" className="axis-label">{fmtCount(peak)}</text>
        <text x="36" y="173" textAnchor="end" className="axis-label">0</text>
      </svg>
      <figcaption>
        {TREND_METRICS.map(metric => (
          <span key={metric.key}>
            <span className="legend-swatch" style={{ background: metric.color }} aria-hidden="true" />
            {metric.label}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
