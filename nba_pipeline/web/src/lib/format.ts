const numbers = new Intl.NumberFormat();
const dates = new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short', day: 'numeric' });

/** Null and undefined render as an em dash so a missing value never reads as zero. */
export function fmtNumber(value: unknown, fractionDigits?: number): string {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  const digits = fractionDigits ?? (Number.isInteger(n) ? 0 : 1);
  return numbers.format(Number(n.toFixed(digits)));
}

export function fmtPercent(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return `${(n * 100).toFixed(1)}%`;
}

export function fmtDate(value: unknown): string {
  if (!value) return '—';
  const raw = String(value);
  // Curated shot dates arrive as either an ISO string or a compact YYYYMMDD value.
  const iso = /^\d{8}$/.test(raw) ? `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6)}` : raw;
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? raw : dates.format(parsed);
}

export function fmtCount(value: unknown): string {
  return fmtNumber(value, 0);
}
