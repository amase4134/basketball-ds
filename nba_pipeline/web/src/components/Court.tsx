import { useMemo, useState } from 'react';
import type { Row, ShotMap } from '../api/types';
import { fmtCount, fmtNumber, fmtPercent } from '../lib/format';

export type BinMetric = 'attempts' | 'makes' | 'fg_pct';

interface CourtProps {
  map: ShotMap;
  metric: BinMetric;
  onMetric: (metric: BinMetric) => void;
  playerNames: Map<number, string>;
}

/** Distinct hues for multi-player point maps; makes and misses stay distinguishable by shape too. */
export const PLAYER_COLORS = ['#2f6f4f', '#8a4b2a', '#3a5a94', '#7a3f6d', '#8a7420', '#2c6f74', '#7d3030', '#4a5570', '#6b5a35', '#3f6a8a', '#6d4a86', '#556b2f'];

const METRICS: { id: BinMetric; label: string }[] = [
  { id: 'attempts', label: 'Attempts' },
  { id: 'makes', label: 'Makes' },
  { id: 'fg_pct', label: 'FG%' },
];

export function Court({ map, metric, onMetric, playerNames }: CourtProps) {
  const [focused, setFocused] = useState<Row | null>(null);
  const grid = map.grid;
  const toX = (value: number) => ((value - grid.x_min) / (grid.x_max - grid.x_min)) * 500;
  const toY = (value: number) => 500 - ((value - grid.y_min) / (grid.y_max - grid.y_min)) * 470;

  const colorFor = useMemo(() => {
    const order = new Map<number, string>();
    map.player_ids.forEach((id, index) => order.set(id, PLAYER_COLORS[index % PLAYER_COLORS.length]));
    return order;
  }, [map.player_ids]);

  const peak = useMemo(() => {
    if (map.mode !== 'bins') return 1;
    return Math.max(1, ...map.items.map(item => Number(item[metric] ?? 0)));
  }, [map.mode, map.items, metric]);

  const describe = (bin: Row) =>
    `${fmtCount(bin.attempts)} attempts, ${fmtCount(bin.makes)} makes, ${fmtPercent(bin.fg_pct)} FG%`;

  return (
    <div className="court-panel">
      {map.mode === 'bins' && (
        <fieldset className="metric-toggle">
          <legend>Shade bins by</legend>
          {METRICS.map(option => (
            <label key={option.id} className="radio-label">
              <input type="radio" name="bin-metric" checked={metric === option.id} onChange={() => onMetric(option.id)} />
              {option.label}
            </label>
          ))}
        </fieldset>
      )}

      <svg
        className="court"
        viewBox="0 0 500 500"
        role="img"
        aria-label={
          map.mode === 'points'
            ? `Shot locations for ${map.player_ids.length} selected player${map.player_ids.length === 1 ? '' : 's'}, ${fmtCount(map.returned)} shots plotted`
            : `Binned shot map shaded by ${metric}, ${fmtCount(map.returned)} bins covering ${fmtCount(map.total_matching)} attempts`
        }
      >
        <rect width="500" height="500" className="court-floor" />
        <g className="court-lines" fill="none" strokeWidth="2">
          <path d="M0 500H500" />
          <path d="M170 500V312.5H330V500" />
          <path d="M190 312.5A60 60 0 0 1 310 312.5" />
          <path d="M232 462.5H268" />
          <path d="M30 500V360.5A237.5 237.5 0 0 1 470 360.5V500" />
          <circle cx="250" cy="450" r="7.5" />
        </g>

        {map.mode === 'points'
          ? map.items.map((shot, index) => {
            const color = colorFor.get(Number(shot.PLAYER_ID)) ?? PLAYER_COLORS[0];
            const cx = toX(Number(shot.LOC_X));
            const cy = toY(Number(shot.LOC_Y));
            const label = `${playerNames.get(Number(shot.PLAYER_ID)) ?? shot.PLAYER_NAME ?? 'Shot'}: ${shot.SHOT_MADE_FLAG ? 'made' : 'missed'} ${fmtNumber(shot.SHOT_DISTANCE, 0)} ft`;
            return shot.SHOT_MADE_FLAG ? (
              <circle key={index} cx={cx} cy={cy} r="3.2" fill={color} fillOpacity="0.85"><title>{label}</title></circle>
            ) : (
              <path
                key={index}
                d={`M${cx - 3} ${cy - 3}l6 6M${cx + 3} ${cy - 3}l-6 6`}
                stroke={color}
                strokeWidth="1.6"
                strokeOpacity="0.75"
                fill="none"
              ><title>{label}</title></path>
            );
          })
          : map.items.map((bin, index) => {
            const value = Number(bin[metric] ?? 0);
            const share = metric === 'fg_pct' ? Math.min(1, value / 0.7) : value / peak;
            return (
              <rect
                key={index}
                x={toX(Number(bin.x))}
                y={toY(Number(bin.y) + grid.cell_size)}
                width={grid.cell_size}
                height={grid.cell_size}
                className="court-bin"
                opacity={0.08 + share * 0.82}
                tabIndex={0}
                role="button"
                aria-label={describe(bin)}
                onFocus={() => setFocused(bin)}
                onMouseEnter={() => setFocused(bin)}
                onClick={() => setFocused(bin)}
              ><title>{describe(bin)}</title></rect>
            );
          })}
      </svg>

      {map.mode === 'bins' && (
        <p className="court-readout" role="status">
          {focused ? describe(focused) : 'Hover, focus, or select a bin to read its attempts, makes, and FG%.'}
        </p>
      )}

      {map.mode === 'points' && map.player_ids.length > 0 && (
        <ul className="court-legend">
          {map.player_ids.map(id => (
            <li key={id}>
              <span className="legend-swatch" style={{ background: colorFor.get(id) }} aria-hidden="true" />
              {playerNames.get(id) ?? `Player ${id}`}
            </li>
          ))}
          <li className="legend-shape"><span aria-hidden="true">●</span> made</li>
          <li className="legend-shape"><span aria-hidden="true">✕</span> missed</li>
        </ul>
      )}
    </div>
  );
}
