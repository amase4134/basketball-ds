import type { AccuracySnapshot, TeamTrajectoryPoint } from '../api/types';
import { fmtDate, fmtNumber } from '../lib/format';

const WIDTH = 480;
const HEIGHT = 200;
const LEFT = 36;
const RIGHT = 10;
const TOP = 16;
const BOTTOM = 30;

const ACCURACY_SERIES = [
  { key: 'mae_preseason_at_gp', label: 'Pre-season MAE', color: 'var(--text-muted)' },
  { key: 'mae_inseason_at_gp', label: 'In-season MAE', color: 'var(--accent)' },
  { key: 'rmse_preseason_at_gp', label: 'Pre-season RMSE', color: 'var(--negative)' },
] as const;

const TEAM_SERIES = [
  { key: 'actual_wins', label: 'Actual wins', color: 'var(--positive)' },
  { key: 'preseason_expected_wins_at_gp', label: 'Expected by pre-season model', color: 'var(--text-muted)' },
  { key: 'inseason_expected_wins_at_gp', label: 'Expected by in-season model', color: 'var(--accent)' },
] as const;

interface Series {
  readonly key: string;
  readonly label: string;
  readonly color: string;
}

function plot<T extends { as_of_date: string }>(points: T[], series: readonly Series[], unit: string, caption: string) {
  const values = points.flatMap(point => series.map(item => Number((point as Record<string, unknown>)[item.key])));
  const finite = values.filter(Number.isFinite);
  const peak = finite.length > 0 ? Math.max(...finite) : 1;
  // Pad the ceiling so the top line never sits flush against the frame.
  const ceiling = Math.max(peak * 1.15, 0.5);

  const x = (index: number) =>
    LEFT + (points.length <= 1 ? (WIDTH - LEFT - RIGHT) / 2 : (index * (WIDTH - LEFT - RIGHT)) / (points.length - 1));
  const y = (value: number) => HEIGHT - BOTTOM - (value / ceiling) * (HEIGHT - TOP - BOTTOM);

  // With one snapshot there is no line to draw, so the dots carry the reading.
  const labelled = points.length <= 6 ? points.map((_, i) => i) : [0, Math.floor((points.length - 1) / 2), points.length - 1];

  return (
    <figure className="trend">
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={caption}>
        <line x1={LEFT} y1={HEIGHT - BOTTOM} x2={WIDTH - RIGHT} y2={HEIGHT - BOTTOM} className="axis" />
        <line x1={LEFT} y1={TOP} x2={LEFT} y2={HEIGHT - BOTTOM} className="axis" />

        {series.map(item => {
          const drawable = points
            .map((point, index) => ({ index, value: Number((point as Record<string, unknown>)[item.key]) }))
            .filter(entry => Number.isFinite(entry.value));
          if (drawable.length === 0) return null;
          return (
            <g key={item.key}>
              {drawable.length > 1 && (
                <polyline
                  fill="none"
                  stroke={item.color}
                  strokeWidth="2"
                  points={drawable.map(entry => `${x(entry.index)},${y(entry.value)}`).join(' ')}
                />
              )}
              {drawable.map(entry => (
                <circle key={entry.index} cx={x(entry.index)} cy={y(entry.value)} r="3" fill={item.color}>
                  <title>{`${fmtDate(points[entry.index].as_of_date)} · ${item.label}: ${fmtNumber(entry.value, 2)}${unit}`}</title>
                </circle>
              ))}
            </g>
          );
        })}

        {labelled.map(index => (
          <text key={index} x={x(index)} y={HEIGHT - 10} textAnchor="middle" className="axis-label">
            {String(points[index].as_of_date).slice(5)}
          </text>
        ))}
        <text x={LEFT - 6} y={TOP + 8} textAnchor="end" className="axis-label">{fmtNumber(ceiling, 1)}</text>
        <text x={LEFT - 6} y={HEIGHT - BOTTOM} textAnchor="end" className="axis-label">0</text>
      </svg>
      <figcaption>
        {series.map(item => (
          <span key={item.key}>
            <span className="legend-swatch legend-dot" style={{ background: item.color }} aria-hidden="true" />
            {item.label}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

/** League-wide model error across every recorded snapshot of the season. */
export function AccuracyTrend({ points }: { points: AccuracySnapshot[] }) {
  if (points.length === 0) {
    return <p className="notice notice-quiet">No accuracy snapshots recorded yet.</p>;
  }
  const scored = points.filter(point => point.mae_preseason_at_gp !== null);
  if (scored.length === 0) {
    return (
      <p className="notice notice-quiet">
        {points.length} snapshot{points.length === 1 ? '' : 's'} recorded, but no games have been played yet,
        so there is no error to chart.
      </p>
    );
  }
  return plot(scored, ACCURACY_SERIES, ' wins', `Model error in wins across ${scored.length} snapshots`);
}

/** One team's banked wins against what each model expected by that date. */
export function TeamTrajectory({ points, team }: { points: TeamTrajectoryPoint[]; team: string }) {
  if (points.length === 0) return <p className="notice notice-quiet">No trajectory recorded for {team} yet.</p>;
  return plot(points, TEAM_SERIES, ' wins', `${team} actual wins versus expected wins across ${points.length} snapshots`);
}
