import type { PageMeta } from '../api/types';
import { fmtCount } from '../lib/format';

interface PagerProps {
  meta: PageMeta;
  onPage: (page: number) => void;
  onPageSize: (size: number) => void;
}

export function Pager({ meta, onPage, onPageSize }: PagerProps) {
  const pages = Math.max(1, Math.ceil(meta.total / meta.page_size));
  const first = meta.total === 0 ? 0 : (meta.page - 1) * meta.page_size + 1;
  const last = Math.min(meta.total, meta.page * meta.page_size);
  return (
    <div className="pager">
      <button type="button" disabled={meta.page <= 1} onClick={() => onPage(meta.page - 1)}>Previous</button>
      <span role="status">
        {fmtCount(first)}–{fmtCount(last)} of {fmtCount(meta.total)} rows · page {meta.page} of {fmtCount(pages)}
      </span>
      <button type="button" disabled={meta.page >= pages} onClick={() => onPage(meta.page + 1)}>Next</button>
      <label className="inline-label">
        Rows per page
        <select value={meta.page_size} onChange={event => onPageSize(Number(event.target.value))}>
          {[25, 50, 100].map(size => <option key={size} value={size}>{size}</option>)}
        </select>
      </label>
    </div>
  );
}
