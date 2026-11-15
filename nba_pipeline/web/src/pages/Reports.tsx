import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { ReportAccent, ReportsList } from '../api/types';
import { AsyncState } from '../components/AsyncState';

const ACCENT_KEY = 'nba-report-accent';
const ACCENTS: { id: ReportAccent; label: string }[] = [
  { id: 'broadcast', label: 'Broadcast' },
  { id: 'scoreboard', label: 'Scoreboard' },
];

function storedAccent(): ReportAccent {
  if (typeof localStorage === 'undefined') return 'broadcast';
  const value = localStorage.getItem(ACCENT_KEY);
  return value === 'scoreboard' ? 'scoreboard' : 'broadcast';
}

export function Reports() {
  const [params, setParams] = useSearchParams();
  const selected = params.get('file') ?? '';
  const [accent, setAccent] = useState<ReportAccent>(storedAccent);

  useEffect(() => {
    const href = '/reports/report-theme.css';
    if (document.querySelector(`link[href="${href}"]`)) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = href;
    document.head.appendChild(link);
  }, []);

  useEffect(() => {
    localStorage.setItem(ACCENT_KEY, accent);
  }, [accent]);

  const reports = useQuery({
    queryKey: ['reports'],
    queryFn: () => api<ReportsList>('/reports'),
  });

  const select = (filename: string) => {
    const next = new URLSearchParams(params);
    if (filename) next.set('file', filename);
    else next.delete('file');
    setParams(next, { replace: true });
  };

  return (
    <div className="reports-gallery" data-accent={accent}>
      <div className={`reports-gallery-layout${selected ? ' has-viewer' : ''}`}>
        <div>
          <header className="report-brand">
            <div className="report-brand-mark">
              <span className="report-brand-name">Basketball Data Science</span>
              <span className="report-brand-sub">Prediction reports</span>
            </div>
            <div className="report-accent-toggle" role="group" aria-label="Accent colorway">
              {ACCENTS.map(option => (
                <button
                  key={option.id}
                  type="button"
                  data-accent-choice={option.id}
                  aria-pressed={accent === option.id}
                  className={accent === option.id ? 'is-active' : undefined}
                  onClick={() => setAccent(option.id)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </header>

          <section className="report-hero">
            <p className="report-kicker">Local archive</p>
            <h1 className="report-title">NBA prediction reports</h1>
            <p className="report-lede">
              Weekly win-predictor digests and Opta comparisons archived under{' '}
              <code>data/meta/reports/</code>. Live projections stay on the{' '}
              <Link to="/predictions">Win Predictor</Link>.
            </p>
          </section>

          <AsyncState query={reports} label="archived reports">
            {data => {
              if (!data.items.length) {
                return (
                  <p className="report-empty">
                    No archived reports yet. Generate one with{' '}
                    <code>python -m nba_pipeline send-win-report --dry-run</code> or{' '}
                    <code>python -m nba_pipeline compare-opta</code>.
                  </p>
                );
              }
              return (
                <section className="report-section">
                  <h2>Archived reports</h2>
                  <p className="report-section-note">
                    {data.meta.count} file{data.meta.count === 1 ? '' : 's'} · accents{' '}
                    {data.meta.accents.join(' / ')}
                  </p>
                  <ul className="report-index-list">
                    {data.items.map(item => (
                      <li key={item.filename}>
                        <a
                          href={`?file=${encodeURIComponent(item.filename)}`}
                          className={selected === item.filename ? 'is-selected' : undefined}
                          onClick={event => {
                            event.preventDefault();
                            select(item.filename);
                          }}
                        >
                          <span>
                            <span className="kind">{item.label}</span>
                            {item.season ? ` · ${item.season}` : ''}
                            <div>{item.filename}</div>
                          </span>
                          <span className="when">{item.as_of ?? '—'}</span>
                        </a>
                      </li>
                    ))}
                  </ul>
                </section>
              );
            }}
          </AsyncState>
        </div>

        {selected && (
          <div>
            <div className="reports-viewer-actions">
              <a className="primary" href={`/api/v1/reports/${encodeURIComponent(selected)}`} target="_blank" rel="noreferrer">
                Open full page
              </a>
              <button type="button" onClick={() => select('')}>Close preview</button>
            </div>
            <div className="reports-viewer">
              <iframe title={selected} src={`/api/v1/reports/${encodeURIComponent(selected)}`} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
