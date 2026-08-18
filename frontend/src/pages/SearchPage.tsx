import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { SearchResponse } from "../api/types";

export function SearchPage() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const [draft, setDraft] = useState(q);
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // submitting the SAME term leaves the url unchanged, so the effect below
  // would not re-run and the search would silently do nothing. bumping this
  // makes an identical re-search a real request — which is also how you see
  // the cache go MISS then HIT.
  const [attempt, setAttempt] = useState(0);

  const run = useCallback(async (term: string) => {
    if (!term.trim()) { setResult(null); return; }
    setBusy(true); setError(null);
    try {
      setResult(await api.search(term));
    } catch (err) {
      setResult(null);
      setError(err instanceof ApiError
        ? (err.problem.status === 429
            ? `Rate limited — retry in ${err.retryAfter ?? 60}s`
            : err.message)
        : "search failed");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { void run(q); }, [q, attempt, run]);

  return (
    <div className="stack">
      <form
        className="searchbar"
        onSubmit={(e) => {
          e.preventDefault();
          setParams({ q: draft });      // keeps the url shareable
          setAttempt((n) => n + 1);     // and forces a run even if q is unchanged
        }}
      >
        <input value={draft} placeholder="Search inside your documents…"
               onChange={(e) => setDraft(e.target.value)} autoFocus />
        <button disabled={busy}>{busy ? "…" : "Search"}</button>
      </form>

      {error && <p className="error">{error}</p>}

      {result && (
        <>
          <div className="meta">
            <strong>{result.total.value}{result.total.relation === "gte" ? "+" : ""}</strong>
            {" results · "}{result.took_ms} ms
            {/* MISS means it reached Elasticsearch; HIT means Redis answered */}
            <span className={`chip ${result.cache.toLowerCase()}`}>{result.cache}</span>
          </div>

          {Object.entries(result.facets).map(([name, buckets]) => buckets.length > 0 && (
            <div className="facets" key={name}>
              <span className="muted">{name}:</span>
              {buckets.map((b) => (
                <span className="chip" key={b.value}>{b.value} · {b.count}</span>
              ))}
            </div>
          ))}

          <ul className="results">
            {result.hits.map((h) => (
              <li key={h.id}>
                <Link to={`/documents/${h.id}`}>{h.title}</Link>
                <span className="score">{h.score.toFixed(2)}</span>
                {/* the server returns <em> around matches; it is our own
                    highlight markup, not user content */}
                {h.snippet && (
                  <p className="snippet"
                     dangerouslySetInnerHTML={{ __html: h.snippet }} />
                )}
              </li>
            ))}
          </ul>

          {result.hits.length === 0 && <p className="muted">Nothing matched.</p>}
        </>
      )}
    </div>
  );
}
