import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { SortDirection } from '../api/types';

export interface RangeFilter {
  min?: string;
  max?: string;
}

export interface TableState {
  season: string;
  sort: string;
  direction: SortDirection;
  page: number;
  pageSize: number;
  players: number[];
  /** When false, selecting players marks them for comparison without hiding other rows. */
  onlySelected: boolean;
  ranges: Record<string, RangeFilter>;
  contains: Record<string, string>;
  enums: Record<string, string>;
  hidden: string[];
  activeFilterCount: number;
}

export interface TableActions {
  setSeason: (season: string) => void;
  toggleSort: (key: string) => void;
  setPage: (page: number) => void;
  setPageSize: (size: number) => void;
  setPlayers: (ids: number[]) => void;
  togglePlayer: (id: number) => void;
  setOnlySelected: (only: boolean) => void;
  /** Both bounds are written together; two navigations in one tick would discard the first. */
  setRange: (key: string, bounds: RangeFilter) => void;
  setContains: (key: string, value: string) => void;
  setEnum: (key: string, value: string) => void;
  clearColumnFilter: (key: string) => void;
  clearFilters: () => void;
  toggleColumn: (key: string) => void;
  showAllColumns: () => void;
}

const numberList = (raw: string | null): number[] =>
  (raw ?? '').split(',').map(part => Number(part.trim())).filter(n => Number.isFinite(n) && n > 0);

/** Decode repeated `COLUMN:VALUE` params into a keyed map. */
const pairs = (values: string[]): Record<string, string> =>
  Object.fromEntries(values.map(entry => [entry.slice(0, entry.indexOf(':')), entry.slice(entry.indexOf(':') + 1)]).filter(([key]) => key));

/** Encode a keyed map back into repeated `COLUMN:VALUE` params. */
const encode = (map: Record<string, string>): string[] =>
  Object.entries(map).filter(([, value]) => value !== '' && value !== undefined).map(([key, value]) => `${key}:${value}`);

export interface TableDefaults {
  sort: string;
  direction?: SortDirection;
  pageSize?: number;
  hidden?: string[];
}

/**
 * Table sort, filter, selection, and column visibility live in the URL so any view
 * can be reloaded or bookmarked (SRS FR-3).
 */
export function useTableState(defaults: TableDefaults): [TableState, TableActions] {
  const [params, setParams] = useSearchParams();

  const state = useMemo<TableState>(() => {
    const ranges: Record<string, RangeFilter> = {};
    for (const [key, value] of Object.entries(pairs(params.getAll('min')))) ranges[key] = { ...ranges[key], min: value };
    for (const [key, value] of Object.entries(pairs(params.getAll('max')))) ranges[key] = { ...ranges[key], max: value };
    const contains = pairs(params.getAll('like'));
    const enums = pairs(params.getAll('is'));
    const players = numberList(params.get('players'));
    const onlySelected = params.get('only') === '1';
    const rangeCount = Object.values(ranges).filter(r => r.min || r.max).length;
    return {
      season: params.get('season') ?? '',
      sort: params.get('sort') ?? defaults.sort,
      direction: params.get('dir') === 'asc' ? 'asc' : 'desc',
      page: Math.max(1, Number(params.get('page') ?? 1) || 1),
      pageSize: [25, 50, 100].includes(Number(params.get('size'))) ? Number(params.get('size')) : defaults.pageSize ?? 50,
      players,
      onlySelected,
      ranges,
      contains,
      enums,
      hidden: params.has('hide') ? (params.get('hide') as string).split(',').filter(Boolean) : defaults.hidden ?? [],
      // Counts column filters only; the player selection is cleared from its own control.
      activeFilterCount: rangeCount + Object.values(contains).filter(Boolean).length + Object.values(enums).filter(Boolean).length,
    };
  }, [params, defaults.sort, defaults.pageSize, defaults.hidden]);

  /** Every mutation resets paging unless it is the page change itself. */
  const write = useCallback((mutate: (next: URLSearchParams) => void, keepPage = false) => {
    setParams(previous => {
      const next = new URLSearchParams(previous);
      mutate(next);
      if (!keepPage) next.delete('page');
      return next;
    }, { replace: true });
  }, [setParams]);

  /**
   * Apply keyed updates to repeated `COLUMN:VALUE` params in a single navigation. The maps are
   * re-read from the pending params rather than from this render's closure, so several columns
   * can be updated at once without one overwriting another.
   */
  const writePairs = useCallback((updates: { param: string; key: string; value: string }[]) => {
    write(next => {
      for (const { param, key, value } of updates) {
        const map = pairs(next.getAll(param));
        if (value === '') delete map[key];
        else map[key] = value;
        next.delete(param);
        encode(map).forEach(entry => next.append(param, entry));
      }
    });
  }, [write]);

  const actions = useMemo<TableActions>(() => ({
    setSeason: season => write(next => next.set('season', season)),
    toggleSort: key => write(next => {
      const sameColumn = (next.get('sort') ?? defaults.sort) === key;
      const current = next.get('dir') === 'asc' ? 'asc' : 'desc';
      next.set('sort', key);
      next.set('dir', sameColumn && current === 'desc' ? 'asc' : 'desc');
    }),
    setPage: page => write(next => next.set('page', String(page)), true),
    setPageSize: size => write(next => next.set('size', String(size))),
    setPlayers: ids => write(next => (ids.length ? next.set('players', ids.join(',')) : next.delete('players'))),
    setOnlySelected: only => write(next => (only ? next.set('only', '1') : next.delete('only'))),
    togglePlayer: id => write(next => {
      const current = numberList(next.get('players'));
      const updated = current.includes(id) ? current.filter(x => x !== id) : [...current, id];
      if (updated.length) next.set('players', updated.join(','));
      else next.delete('players');
    }),
    setRange: (key, bounds) => writePairs([
      { param: 'min', key, value: bounds.min ?? '' },
      { param: 'max', key, value: bounds.max ?? '' },
    ]),
    setContains: (key, value) => writePairs([{ param: 'like', key, value }]),
    setEnum: (key, value) => writePairs([{ param: 'is', key, value }]),
    clearColumnFilter: key => write(next => {
      for (const param of ['min', 'max', 'like', 'is']) {
        const map = pairs(next.getAll(param));
        delete map[key];
        next.delete(param);
        encode(map).forEach(entry => next.append(param, entry));
      }
    }),
    clearFilters: () => write(next => ['min', 'max', 'like', 'is'].forEach(param => next.delete(param))),
    toggleColumn: key => write(next => {
      const current = next.has('hide') ? (next.get('hide') as string).split(',').filter(Boolean) : defaults.hidden ?? [];
      const updated = current.includes(key) ? current.filter(x => x !== key) : [...current, key];
      next.set('hide', updated.join(','));
    }, true),
    showAllColumns: () => write(next => next.set('hide', ''), true),
  }), [write, writePairs, defaults.sort, defaults.hidden]);

  return [state, actions];
}

/** Translate table state into the repeated query parameters the API expects. */
export function filterParams(state: TableState) {
  return {
    min_stat: Object.entries(state.ranges).filter(([, r]) => r.min).map(([key, r]) => `${key}:${r.min}`),
    max_stat: Object.entries(state.ranges).filter(([, r]) => r.max).map(([key, r]) => `${key}:${r.max}`),
    contains: Object.entries(state.contains).filter(([, value]) => value).map(([key, value]) => `${key}:${value}`),
    player_ids: state.players.join(','),
    sort: state.sort,
    direction: state.direction,
    page: state.page,
    page_size: state.pageSize,
  };
}
