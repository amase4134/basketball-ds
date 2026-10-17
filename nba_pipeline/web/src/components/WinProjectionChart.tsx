import type { WinPredictionRow } from '../api/types';
import { fmtNumber } from '../lib/format';

const ROW_HEIGHT = 20;
const LABEL_WIDTH = 46;
const PLOT_WIDTH = 420;
const TOP = 26;

const SERIES = [
  { key: 'preseason_pred_wins', label: 'Pre-season projection', color: 'var(--text-muted)' },
  { key: 'updated_pred_wins', label: 'Updated projection', color: 'var(--accent)' },
  { key: 'current_82_pace', label: 'Current 82-game pace', color: 'var(--positive)' },
] as const;

/**
 * A dumbbell chart: one row per team, a connector spanning the projections and
 * a marker per series, so the gap between forecast and reality is the thing the
 * eye lands on first.
 */
export function WinProjectionChart({ rows, started }: { rows: WinPredictionRow[]; started: boolean }) {
  if (rows.length === 0) return <p className="notice notice-quiet">No projections to chart yet.</p>;

  const series = started ? SERIES : SERIES.filter(item => item.key === 'preseason_pred_wins');
  const ordered = [...rows].sort((a, b) => b.updated_pred_wins - a.updated_pred_wins);
  const height = TOP + ordered.length * ROW_HEIGHT + 18;
  const width = LABEL_WIDTH + PLOT_WIDTH + 34;

  // A fixed 0–82 domain keeps the axis comparable from one week to the next.
  const x = (wins: number) => LABEL_WIDTH + (Math.min(Math.max(wins, 0), 82) / 82) * PLOT_WIDTH;
  const ticks = [0, 20, 41, 60, 82];

  return (
    <figure className="trend win-chart">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={
          started
            ? `Projected and actual 82-game win pace for ${ordered.length} teams`
            : `Pre-season 82-game win projection for ${ordered.length} teams`
        }
      >
        {ticks.map(tick => (
          <g key={tick}>
            <line x1={x(tick)} y1={TOP - 8} x2={x(tick)} y2={height - 18} className="axis" strokeOpacity={tick === 41 ? 0.9 : 0.3} />
            <text x={x(tick)} y={TOP - 12} textAnchor="middle" className="axis-label">{tick}</text>
          </g>
        ))}

        {ordered.map((row, index) => {
          const y = TOP + index * ROW_HEIGHT + ROW_HEIGHT / 2;
          const values = series
            .map(item => ({ ...item, value: Number(row[item.key] ?? Number.NaN) }))
            .filter(item => Number.isFinite(item.value));
          const low = Math.min(...values.map(item => item.value));
          const high = Math.max(...values.map(item => item.value));
          const team = row.TEAM_ABBREVIATION ?? String(row.TEAM_ID);
          const summary = values.map(item => `${item.label} ${fmtNumber(item.value, 1)}`).join(', ');

          return (
            <g key={row.TEAM_ID}>
              <text x={LABEL_WIDTH - 8} y={y + 4} textAnchor="end" className="win-chart-team">{team}</text>
              {index % 2 === 1 && (
                <rect x={LABEL_WIDTH} y={y - ROW_HEIGHT / 2} width={PLOT_WIDTH} height={ROW_HEIGHT} className="win-chart-stripe" />
              )}
              {values.length > 1 && (
                <line x1={x(low)} y1={y} x2={x(high)} y2={y} className="win-chart-link" />
              )}
              {values.map(item => (
                <circle key={item.key} cx={x(item.value)} cy={y} r="4" fill={item.color} stroke="var(--surface)" strokeWidth="1" />
              ))}
              <text x={LABEL_WIDTH + PLOT_WIDTH + 6} y={y + 4} className="axis-label">
                {fmtNumber(row.updated_pred_wins, 1)}
              </text>
              <title>{`${row.TEAM_NAME ?? team}: ${summary}`}</title>
            </g>
          );
        })}
      </svg>
      <figcaption>
        {series.map(item => (
          <span key={item.key}>
            <span className="legend-swatch legend-dot" style={{ background: item.color }} aria-hidden="true" />
            {item.label}
          </span>
        ))}
        <span>Dashed line marks a 41-win .500 season.</span>
      </figcaption>
    </figure>
  );
}
