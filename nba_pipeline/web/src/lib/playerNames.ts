import { useEffect, useSyncExternalStore } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, queryString } from '../api/client';
import type { PlayerOption } from '../api/types';

/**
 * Player names appear in chips, comparison headers, and the court legend, but a selection may
 * arrive as bare ids from a bookmarked URL. Every view that already knows a name contributes it
 * here, so a player picked from a table or typeahead is labelled immediately and only ids that
 * nothing has seen yet need a lookup request.
 */
const names = new Map<number, string>();
const listeners = new Set<() => void>();
let snapshot: ReadonlyMap<number, string> = new Map();

function publish() {
  snapshot = new Map(names);
  listeners.forEach(listener => listener());
}

export function rememberPlayerNames(entries: Iterable<readonly [number, string]>) {
  let changed = false;
  for (const [id, name] of entries) {
    if (Number.isFinite(id) && name && names.get(id) !== name) {
      names.set(id, name);
      changed = true;
    }
  }
  if (changed) publish();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Resolve any still-unknown ids, then return the id/name map for the requested selection. */
export function usePlayerNames(ids: number[]): ReadonlyMap<number, string> {
  const cached = useSyncExternalStore(subscribe, () => snapshot);
  const missing = ids.filter(id => !cached.has(id));
  const lookupKey = missing.join(',');

  const lookup = useQuery({
    queryKey: ['player-names', lookupKey],
    queryFn: () => api<{ items: PlayerOption[] }>(`/lookups/players?${queryString({ ids: lookupKey })}`),
    enabled: missing.length > 0,
    staleTime: Infinity,
  });

  useEffect(() => {
    if (lookup.data) rememberPlayerNames(lookup.data.items.map(item => [item.PLAYER_ID, item.FULL_NAME] as const));
  }, [lookup.data]);

  return cached;
}
