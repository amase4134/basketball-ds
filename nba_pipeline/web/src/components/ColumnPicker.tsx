import type { ColumnDef } from '../lib/columns';
import type { TableActions, TableState } from '../lib/useTableState';
import { usePopover } from '../lib/usePopover';

interface ColumnPickerProps {
  columns: ColumnDef[];
  state: TableState;
  actions: TableActions;
}

/** Show/hide non-essential columns (SRS FR-24). */
export function ColumnPicker({ columns, state, actions }: ColumnPickerProps) {
  const { open, setOpen, ref } = usePopover();
  const optional = columns.filter(column => !column.essential);
  const shown = optional.filter(column => !state.hidden.includes(column.key)).length;

  return (
    <div className="column-picker" ref={ref}>
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
        Columns ({shown}/{optional.length})
      </button>
      {open && (
        <div className="popover popover-wide" role="dialog" aria-label="Choose visible columns">
          <p className="popover-title">Visible columns</p>
          <ul className="column-list">
            {optional.map(column => (
              <li key={column.key}>
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={!state.hidden.includes(column.key)}
                    onChange={() => actions.toggleColumn(column.key)}
                  />
                  {column.label}
                </label>
              </li>
            ))}
          </ul>
          <div className="popover-actions">
            <button type="button" onClick={actions.showAllColumns}>Show all</button>
          </div>
        </div>
      )}
    </div>
  );
}
