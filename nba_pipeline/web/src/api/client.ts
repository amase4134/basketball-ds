export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly dataset?: string) {
    super(message);
  }
}

/** Fetch a local API resource, normalising both error envelopes the API can return. */
export async function api<T>(path: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const message = body?.error?.message || body?.detail || `Request failed (${response.status})`;
    throw new ApiError(typeof message === 'string' ? message : 'Request failed', response.status, body?.error?.dataset);
  }
  return response.json();
}

/** Build a query string, dropping empty values and expanding arrays into repeated keys. */
export function queryString(params: Record<string, string | number | string[] | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    if (Array.isArray(value)) value.forEach(entry => entry && search.append(key, entry));
    else search.set(key, String(value));
  }
  return search.toString();
}
