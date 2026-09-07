import { useEffect, useState } from 'react';
import type { ColumnDef } from '../lib/columns';
import type { TableActions, TableState } from '../lib/useTableState';
import { usePopover } from '../lib/usePopover';

interface ColumnFilterProps {
  column: ColumnDef;
  state: TableState;
  actions: TableActions;
}

/**
 * Per-column filter popover. Ranges and text are committed on Apply/Enter rather than on
 * every keystroke so a partially typed bound never triggers a query.
 */
export function ColumnFilter({ column, state, actions }: ColumnFilterProps) {
  const { open, setOpen, ref } = usePopover();
  const range = state.ranges[column.key] ?? {};
  const text = state.contains[column.key] ?? '';
  const choice = state.enums[column.key] ?? '';
  const active = Boolean(range.min || range.max || text || choice);

  const [draft, setDraft] = useState({ min: range.min ?? '', max: range.max ?? '', text });
  useEffect(() => {
    if (open) setDraft({ min: range.min ?? '', max: range.max ?? '', text });
  }, [open, range.min, range.max, text]);

  const apply = () => {
    if (column.filter === 'range') {
      actions.setRange(column.key, { min: draft.min.trim(), max: draft.max.trim() });
    } else if (column.filter === 'text') {
      actions.setContains(column.key, draft.text.trim());
    }
    setOpen(false);
  };

  const clear = () => {
    setDraft({ min: '', max: '', text: '' });
    actions.clearColumnFilter(column.key);
    setOpen(false);
  };

  const summary = active
    ? [range.min && `≥ ${range.min}`, range.max && `≤ ${range.max}`, text && `“${text}”`, choice && column.options?.find(o => o.value === choice)?.label]
      .filter(Boolean).join(' · ')
    : 'No filter';

  return (
    <div className="column-filter" ref={ref}>
      <button
        type="button"
        className={active ? 'filter-toggle is-active' : 'filter-toggle'}
        aria-expanded={open}
        aria-label={`Filter ${column.label}: ${summary}`}
        title={`Filter ${column.label} — ${summary}`}
        onClick={() => setOpen(!open)}
      >
        <span aria-hidden="true">{active ? '▣' : '▽'}</span>
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label={`${column.label} filter`}>
          <p className="popover-title">{column.label}</p>
          {column.hint && <p className="popover-hint">{column.hint}</p>}

          {column.filter === 'range' && (
            <div className="range-inputs">
              <label>
                Minimum
                <input
                  type="number"
                  step="any"
                  inputMode="decimal"
                  value={draft.min}
                  onChange={event => setDraft({ ...draft, min: event.target.value })}
                  onKeyDown={event => event.key === 'Enter' && apply()}
                />
              </label>
              <label>
                Maximum
                <input
                  type="number"
                  step="any"
                  inputMode="decimal"
                  value={draft.max}
                  onChange={event => setDraft({ ...draft, max: event.target.value })}
                  onKeyDown={event => event.key === 'Enter' && apply()}
                />
              </label>
            </div>
          )}

          {column.filter === 'text' && (
            <label>
              Contains
              <input
                type="search"
                value={draft.text}
                placeholder={`Search ${column.label.toLowerCase()}`}
                onChange={event => setDraft({ ...draft, text: event.target.value })}
                onKeyDown={event => event.key === 'Enter' && apply()}
              />
            </label>
          )}

          {column.filter === 'enum' && (
            <label>
              Show
              <select
                value={choice}
                onChange={event => {
                  actions.setEnum(column.key, event.target.value);
                  setOpen(false);
                }}
              >
                <option value="">All rows</option>
                {column.options?.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
          )}

          <div className="popover-actions">
            {column.filter !== 'enum' && <button type="button" onClick={apply}>Apply</button>}
            <button type="button" className="ghost" onClick={clear} disabled={!active}>Clear</button>
          </div>
        </div>
      )}
    </div>
  );
}
