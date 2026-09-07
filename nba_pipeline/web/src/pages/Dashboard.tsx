import { Link, useSearchParams } from 'react-router-dom';
import logo from '../assets/logo.jpg';
import { AsyncState } from '../components/AsyncState';
import { SeasonSelect } from '../components/SeasonSelect';
import { StatCards } from '../components/StatCards';
import { fmtCount, fmtDate } from '../lib/format';
import { useCatalog } from '../lib/useCatalog';

export function Dashboard() {
  const catalog = useCatalog();
  const [params, setParams] = useSearchParams();

  return (
    <>
      <img className="hero-logo" src={logo} alt="Basketball Data Science" width={1024} height={1024} />
      <h1>Local NBA data</h1>
      <AsyncState query={catalog} label="the data catalog">
        {data => {
          const season = data.seasons.includes(params.get('season') ?? '') ? (params.get('season') as string) : data.seasons[0];
          const latest = data.refresh_log?.[0];
          return (
            <>
              <div className="filters">
                <SeasonSelect
                  seasons={data.seasons}
                  value={season}
                  onChange={next => setParams(new URLSearchParams({ season: next }), { replace: true })}
                />
              </div>

              <StatCards
                cards={Object.entries(data.datasets).map(([name, dataset]) => ({
                  label: name.replaceAll('_', ' '),
                  value: fmtCount(dataset.rows),
                  note: dataset.available ? 'rows available' : 'unavailable',
                }))}
              />

              <h2>Coverage and freshness</h2>
              <ul className="facts">
                <li>{data.seasons.length} seasons discovered: {data.seasons.join(', ')}</li>
                <li>{data.shot_coverage}</li>
                <li>
                  Court bins are fixed {data.grid.cell_size}-unit cells from x {data.grid.x_min}…{data.grid.x_max} and
                  y {data.grid.y_min}…{data.grid.y_max}; point maps are capped at {fmtCount(data.grid.point_limit)} shots.
                </li>
                {latest && (
                  <li>
                    Last refresh entry: {String(latest.dataset ?? 'unknown dataset')}
                    {latest.season ? ` (${latest.season})` : ''} — {String(latest.status ?? 'unknown status')}
                    {latest.timestamp ? ` on ${fmtDate(latest.timestamp)}` : ''}
                  </li>
                )}
              </ul>

              <p className="selection-actions">
                <Link className="button" to={`/players?season=${season}`}>Explore players</Link>
                <Link className="button" to={`/shots?season=${season}`}>Explore shots</Link>
              </p>
            </>
          );
        }}
      </AsyncState>
    </>
  );
}
