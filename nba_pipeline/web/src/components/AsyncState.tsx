import React from 'react';
import type { UseQueryResult } from '@tanstack/react-query';

interface AsyncStateProps<T> {
  query: UseQueryResult<T>;
  label?: string;
  children: (data: T) => React.ReactNode;
}

/** Renders the loading, failure, and success states every asynchronous view needs (SRS FR-4). */
export function AsyncState<T>({ query, label = 'data', children }: AsyncStateProps<T>) {
  if (query.isPending) {
    return <p className="notice" role="status">Loading {label}…</p>;
  }
  if (query.isError) {
    const message = query.error instanceof Error ? query.error.message : `Could not load ${label}.`;
    return (
      <div className="notice notice-error" role="alert">
        <p>{message}</p>
        <button type="button" onClick={() => query.refetch()}>Retry</button>
      </div>
    );
  }
  return <>{children(query.data as T)}</>;
}
