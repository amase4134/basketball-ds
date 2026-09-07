import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { Catalog } from '../api/types';

export function useCatalog() {
  return useQuery({
    queryKey: ['catalog'],
    queryFn: () => api<Catalog>('/catalog'),
    staleTime: 60_000,
  });
}

/**
 * Views default to the newest discovered season. The value is written back to the URL so a
 * reloaded or shared link resolves to the same data instead of drifting with new refreshes.
 */
export function useResolvedSeason(catalog: Catalog | undefined, season: string, setSeason: (season: string) => void) {
  const fallback = catalog?.seasons?.[0] ?? '';
  const known = catalog?.seasons?.includes(season) ?? false;
  useEffect(() => {
    if (fallback && !known) setSeason(fallback);
  }, [fallback, known, setSeason]);
  return known ? season : fallback;
}
