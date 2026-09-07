import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api, queryString } from '../api/client';
import type { Paged, ShotMap, ShotSummary } from '../api/types';
import { usePlayerNames } from '../lib/playerNames';
import { AsyncState } from '../components/AsyncState';
import { Court, type BinMetric } from '../components/Court';
import { DataTable } from '../components/DataTable';
import { Pager } from '../components/Pager';
import { PlayerMultiSelect } from '../components/PlayerMultiSelect';
import { SeasonSelect } from '../components/SeasonSelect';
import { StatCards } from '../components/StatCards';
import { SHOT_COLUMNS, SHOT_DEFAULT_HIDDEN } from '../lib/columns';
import { fmtCount, fmtNumber, fmtPercent } from '../lib/format';
import { useCatalog, useResolvedSeason } from '../lib/useCatalog';
import { filterParams, useTableState } from '../lib/useTableState';

const DEFAULTS = { sort: 'GAME_DATE', pageSize: 25, hidden: SHOT_DEFAULT_HIDDEN };

export function Shots() {
  const catalog = useCatalog();
  const [state, actions] = useTableState(DEFAULTS);
  const [params, setParams] = useSearchParams();
  const season = useResolvedSeason(catalog.data, state.season, actions.setSeason);
  const maxPlayers = catalog.data?.limits?.max_selected_players ?? 12;
  const metric = (params.get('metric') ?? 'attempts') as BinMetric;
  const setMetric = (next: BinMetric) => {
    const updated = new URLSearchParams(params);
    updated.set('metric', next);
    setParams(updated, { replace: true });
  };

  const table = filterParams(state);
  // Column-level enum filters map onto the API's dedicated shot predicates.
  const scope = {
    season,
    player_ids: state.players.join(','),
    result: state.enums.SHOT_MADE_FLAG ?? '',
    shot_type: state.enums.SHOT_TYPE ?? '',
    min_stat: table.min_stat,
    max_stat: table.max_stat,
  };

  const summary = useQuery({
    queryKey: ['shot-summary', scope],
    queryFn: () => api<ShotSummary>(`/shots/summary?${queryString(scope)}`),
    enabled: Boolean(season),
  });
  const map = useQuery({
    queryKey: ['shot-map', scope, metric],
    queryFn: () => api<ShotMap>(`/shots/map?${queryString({ ...scope, metric })}`),
    enabled: Boolean(season),
  });
  const shots = useQuery({
    queryKey: ['shots', scope, table.sort, table.direction, table.page, table.page_size],
    queryFn: () => api<Paged>(`/shots?${queryString({ ...scope, sort: table.sort, direction: table.direction, page: table.page, page_size: table.page_size })}`),
    enabled: Boolean(season),
    placeholderData: previous => previous,
  });

  const playerNames = usePlayerNames(state.players);

  return (
    <>
      <h1>Shot explorer</h1>
      <p className="lede">{catalog.data?.shot_coverage ?? 'Regular-season field-goal attempts.'}</p>

      <div className="filters">
        <AsyncState query={catalog} label="seasons">
          {data => <SeasonSelect seasons={data.seasons} value={season} onChange={actions.setSeason} />}
        </AsyncState>
        <PlayerMultiSelect season={season} selected={state.players} onChange={actions.setPlayers} max={maxPlayers} />
      </div>

      <AsyncState query={summary} label="shot totals">
        {data => (
          <StatCards
            cards={[
              { label: 'FGA', value: fmtCount(data.fga) },
              { label: 'FGM', value: fmtCount(data.fgm) },
              { label: 'FG%', value: fmtPercent(data.fg_pct) },
              { label: '2P%', value: fmtPercent(data.two_pct), note: `${fmtCount(data.two_pa)} attempts` },
              { label: '3P%', value: fmtPercent(data.three_pct), note: `${fmtCount(data.three_pa)} attempts` },
              { label: 'Avg. distance', value: `${fmtNumber(data.avg_distance, 1)} ft` },
              { label: 'Players in scope', value: fmtCount(data.selected_player_count) },
            ]}
          />
        )}
      </AsyncState>

      <AsyncState query={map} label="the shot map">
        {data => (
          <>
            <p className="notice notice-quiet" role="status">
              {data.mode === 'points'
                ? `Plotting ${fmtCount(data.returned)} individual shots for the selected players.`
                : `Aggregated into ${fmtCount(data.returned)} fixed ${data.grid.cell_size}-unit bins covering ${fmtCount(data.total_matching)} attempts. Select up to ${maxPlayers} players to plot individual shots below ${fmtCount(data.grid.point_limit)} attempts.`}
            </p>
            <Court map={data} metric={metric} onMetric={setMetric} playerNames={playerNames} />
          </>
        )}
      </AsyncState>

      <h2>Shot results</h2>
      <AsyncState query={shots} label="shot rows">
        {data => (
          <>
            <p className="result-count" role="status">{fmtCount(data.meta.total)} matching shots</p>
            <DataTable
              columns={SHOT_COLUMNS}
              rows={data.items}
              state={state}
              actions={actions}
              caption="Individual shot attempts, sortable and filterable by column"
              rowKey={(row, index) => `${row.GAME_ID}-${row.PLAYER_ID}-${index}`}
              emptyMessage="No shots match the current filters."
            />
            <Pager meta={data.meta} onPage={actions.setPage} onPageSize={actions.setPageSize} />
          </>
        )}
      </AsyncState>
    </>
  );
}
