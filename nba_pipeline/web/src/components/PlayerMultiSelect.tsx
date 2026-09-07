import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, queryString } from '../api/client';
import type { PlayerOption } from '../api/types';
import { rememberPlayerNames, usePlayerNames } from '../lib/playerNames';

interface PlayerMultiSelectProps {
  season: string;
  selected: number[];
  onChange: (ids: number[]) => void;
  max: number;
}

/** Debounce the typeahead so each keystroke does not become a lookup request. */
function useDebounced(value: string, delay = 200) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export function PlayerMultiSelect({ season, selected, onChange, max }: PlayerMultiSelectProps) {
  const [search, setSearch] = useState('');
  const term = useDebounced(search.trim());
  const inputRef = useRef<HTMLInputElement>(null);
  const atCapacity = selected.length >= max;

  const suggestions = useQuery({
    queryKey: ['player-lookup', season, term],
    queryFn: () => api<{ items: PlayerOption[] }>(`/lookups/players?${queryString({ q: term, season })}`),
    enabled: term.length > 0,
  });

  const names = usePlayerNames(selected);
  const labelFor = (id: number) => names.get(id) ?? 'Loading…';

  const add = (option: PlayerOption) => {
    rememberPlayerNames([[option.PLAYER_ID, option.FULL_NAME]]);
    if (!selected.includes(option.PLAYER_ID) && !atCapacity) onChange([...selected, option.PLAYER_ID]);
    setSearch('');
    inputRef.current?.focus();
  };

  const results = (suggestions.data?.items ?? []).filter(item => !selected.includes(item.PLAYER_ID));

  return (
    <div className="multi-select">
      <label htmlFor="player-multi-search">
        Players
        <input
          id="player-multi-search"
          ref={inputRef}
          type="search"
          role="combobox"
          aria-expanded={term.length > 0}
          aria-controls="player-multi-results"
          autoComplete="off"
          placeholder={atCapacity ? `Selection full (${max} players)` : 'Search and add players'}
          value={search}
          disabled={atCapacity && search === ''}
          onChange={event => setSearch(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter' && results.length > 0) {
              event.preventDefault();
              add(results[0]);
            }
            if (event.key === 'Backspace' && search === '' && selected.length > 0) {
              onChange(selected.slice(0, -1));
            }
          }}
        />
      </label>

      {term.length > 0 && (
        <ul id="player-multi-results" className="lookup-results" role="listbox" aria-label="Player search results">
          {suggestions.isPending && <li className="lookup-note">Searching players…</li>}
          {suggestions.isError && <li className="lookup-note">Player lookup is unavailable.</li>}
          {suggestions.isSuccess && results.length === 0 && <li className="lookup-note">No further matches.</li>}
          {results.map(item => (
            <li key={item.PLAYER_ID}>
              <button type="button" role="option" aria-selected={false} onClick={() => add(item)}>
                {item.FULL_NAME}
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="chips" role="group" aria-label={`${selected.length} selected players`}>
        {selected.length === 0 && <span className="chips-empty">No players selected — showing all rows.</span>}
        {selected.map(id => (
          <span key={id} className="chip">
            {labelFor(id)}
            <button type="button" aria-label={`Remove ${labelFor(id)}`} onClick={() => onChange(selected.filter(x => x !== id))}>
              ×
            </button>
          </span>
        ))}
        {selected.length > 1 && (
          <button type="button" className="ghost chip-clear" onClick={() => onChange([])}>Clear all</button>
        )}
      </div>
      {atCapacity && <p className="hint" role="status">Selection is capped at {max} players. Remove one to add another.</p>}
    </div>
  );
}
