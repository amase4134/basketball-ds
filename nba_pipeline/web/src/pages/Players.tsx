import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api, queryString } from '../api/client';
import type { Paged, Row } from '../api/types';
import { AsyncState } from '../components/AsyncState';
import { DataTable } from '../components/DataTable';
import { Pager } from '../components/Pager';
import { PlayerMultiSelect } from '../components/PlayerMultiSelect';
import { SeasonSelect } from '../components/SeasonSelect';
import { PlayerComparison } from '../components/PlayerComparison';
import { PLAYER_COLUMNS, PLAYER_DEFAULT_HIDDEN } from '../lib/columns';
import { fmtCount } from '../lib/format';
import { useCatalog, useResolvedSeason } from '../lib/useCatalog';
import { filterParams, useTableState } from '../lib/useTableState';

const DEFAULTS = { sort: 'PTS', pageSize: 50, hidden: PLAYER_DEFAULT_HIDDEN };

export function Players() {
  const navigate = useNavigate();
  const catalog = useCatalog();
  const [state, actions] = useTableState(DEFAULTS);
  const season = useResolvedSeason(catalog.data, state.season, actions.setSeason);
  const maxPlayers = catalog.data?.limits?.max_selected_players ?? 12;

  const params = {
    ...filterParams(state),
    season,
    // The selection marks players for comparison; it only narrows the table on request.
    player_ids: state.onlySelected ? state.players.join(',') : '',
  };
  const table = useQuery({
    queryKey: ['players', params],
    queryFn: () => api<Paged>(`/players?${queryString(params)}`),
    enabled: Boolean(season),
    placeholderData: previous => previous,
  });

  const openPlayer = (row: Row) => navigate(`/players/${row.PLAYER_ID}?season=${row.season}`);

  return (
    <>
      <h1>Player statistics</h1>
      <p className="lede">
        Rates are per 100 possessions; <abbr title="Minutes">MIN</abbr> is a season total. Players traded mid-season
        appear once per team.
      </p>

      <div className="filters">
        <AsyncState query={catalog} label="seasons">
          {data => <SeasonSelect seasons={data.seasons} value={season} onChange={actions.setSeason} />}
        </AsyncState>
        <PlayerMultiSelect season={season} selected={state.players} onChange={actions.setPlayers} max={maxPlayers} />
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={state.onlySelected}
            disabled={state.players.length === 0}
            onChange={event => actions.setOnlySelected(event.target.checked)}
          />
          Limit table to selected players
        </label>
      </div>

      {state.players.length > 0 && (
        <p className="selection-actions">
          <Link className="button" to={`/shots?season=${season}&players=${state.players.join(',')}`}>
            Open {state.players.length === 1 ? 'this player' : `these ${state.players.length} players`} in Shot Explorer
          </Link>
        </p>
      )}

      <PlayerComparison season={season} players={state.players} />

      <AsyncState query={table} label="player statistics">
        {data => (
          <>
            <p className="result-count" role="status">{fmtCount(data.meta.total)} matching team-season rows</p>
            <DataTable
              columns={PLAYER_COLUMNS}
              rows={data.items}
              state={state}
              actions={actions}
              caption="Player season statistics, sortable and filterable by column"
              rowKey={row => `${row.PLAYER_ID}-${row.TEAM_ID}-${row.season}`}
              linkColumn={{ key: 'PLAYER_NAME', onActivate: openPlayer, describe: row => `${row.PLAYER_NAME} details` }}
              selection={{
                isSelected: row => state.players.includes(Number(row.PLAYER_ID)),
                onToggle: row => actions.togglePlayer(Number(row.PLAYER_ID)),
                label: row => String(row.PLAYER_NAME),
                isDisabled: () => state.players.length >= maxPlayers,
                atCapacityMessage: `At most ${maxPlayers} players can be selected`,
              }}
            />
            <Pager meta={data.meta} onPage={actions.setPage} onPageSize={actions.setPageSize} />
          </>
        )}
      </AsyncState>
    </>
  );
}
