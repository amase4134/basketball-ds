import { useMemo } from 'react';
import type { Row } from '../api/types';
import { renderCell, type ColumnDef } from '../lib/columns';
import type { TableActions, TableState } from '../lib/useTableState';
import { ColumnFilter } from './ColumnFilter';
import { ColumnPicker } from './ColumnPicker';

export interface RowSelection {
  isSelected: (row: Row) => boolean;
  onToggle: (row: Row) => void;
  label: (row: Row) => string;
  /** Rows past the selection cap stay clickable only if already selected. */
  isDisabled?: (row: Row) => boolean;
  atCapacityMessage?: string;
}

interface DataTableProps {
  columns: ColumnDef[];
  rows: Row[];
  state: TableState;
  actions: TableActions;
  caption: string;
  rowKey: (row: Row, index: number) => string;
  selection?: RowSelection;
  /** Turns one column's cells into buttons that open a detail view. */
  linkColumn?: { key: string; onActivate: (row: Row) => void; describe: (row: Row) => string };
  emptyMessage?: string;
}

const ARROW = { asc: '↑', desc: '↓' } as const;

export function DataTable({
  columns, rows, state, actions, caption, rowKey, selection, linkColumn, emptyMessage = 'No rows match the current filters.',
}: DataTableProps) {
  const visible = useMemo(() => columns.filter(column => column.essential || !state.hidden.includes(column.key)), [columns, state.hidden]);

  return (
    <div className="data-table">
      <div className="table-toolbar">
        <ColumnPicker columns={columns} state={state} actions={actions} />
        {state.activeFilterCount > 0 && (
          <button type="button" className="ghost" onClick={actions.clearFilters}>
            Clear {state.activeFilterCount} filter{state.activeFilterCount === 1 ? '' : 's'}
          </button>
        )}
        <span className="toolbar-note">
          Sorted by {columns.find(c => c.key === state.sort)?.label ?? state.sort} ({state.direction === 'asc' ? 'ascending' : 'descending'})
        </span>
      </div>

      <div className="table-wrap">
        <table>
          <caption className="visually-hidden">{caption}</caption>
          <thead>
            <tr>
              {selection && <th scope="col" className="select-cell"><span className="visually-hidden">Select</span></th>}
              {visible.map(column => {
                const sorted = state.sort === column.key;
                return (
                  <th
                    key={column.key}
                    scope="col"
                    className={column.numeric ? 'numeric' : undefined}
                    aria-sort={sorted ? (state.direction === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <div className="th-content">
                      {column.sortable ? (
                        <button type="button" className="sort-toggle" onClick={() => actions.toggleSort(column.key)}>
                          <span>{column.label}</span>
                          <span className="sort-arrow" aria-hidden="true">{sorted ? ARROW[state.direction] : '↕'}</span>
                          <span className="visually-hidden">
                            {sorted ? `sorted ${state.direction === 'asc' ? 'ascending' : 'descending'}, activate to reverse` : 'activate to sort'}
                          </span>
                        </button>
                      ) : (
                        <span className="th-label">{column.label}</span>
                      )}
                      {column.filter && <ColumnFilter column={column} state={state} actions={actions} />}
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={visible.length + (selection ? 1 : 0)} className="empty-cell">{emptyMessage}</td>
              </tr>
            )}
            {rows.map((row, index) => {
              const selected = selection?.isSelected(row) ?? false;
              const disabled = selection?.isDisabled?.(row) ?? false;
              return (
                <tr key={rowKey(row, index)} className={selected ? 'is-selected' : undefined}>
                  {selection && (
                    <td className="select-cell">
                      <input
                        type="checkbox"
                        checked={selected}
                        disabled={disabled && !selected}
                        title={disabled && !selected ? selection.atCapacityMessage : undefined}
                        aria-label={`Select ${selection.label(row)}`}
                        onChange={() => selection.onToggle(row)}
                      />
                    </td>
                  )}
                  {visible.map(column => (
                    <td key={column.key} className={column.numeric ? 'numeric' : undefined}>
                      {linkColumn?.key === column.key ? (
                        <button
                          type="button"
                          className="link-cell"
                          aria-label={`Open ${linkColumn.describe(row)}`}
                          onClick={() => linkColumn.onActivate(row)}
                        >
                          {renderCell(column, row[column.key])}
                        </button>
                      ) : (
                        renderCell(column, row[column.key])
                      )}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
