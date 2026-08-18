import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { DocumentListResponse, Status } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import { formatBytesShort } from "../lib/format";

const PAGE_SIZES = [10, 25, 50, 100];   // 100 is the server's cap
const FILTERS: (Status | "")[] = ["", "LIVE", "PENDING", "FAILED"];

export function DocumentsPage() {
  const [data, setData] = useState<DocumentListResponse | null>(null);
  const [status, setStatus] = useState<Status | "">("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.listDocuments(page, size, status || undefined));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "could not load documents");
    }
  }, [page, size, status]);

  useEffect(() => { void load(); }, [load]);

  // anything still in flight will change state shortly, so keep it fresh
  const inFlight = data?.items.some((d) => d.status === "PENDING") ?? false;
  useEffect(() => {
    if (!inFlight) return;
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, [inFlight, load]);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;

  const pages = Math.max(1, Math.ceil(data.total / size));
  const first = data.total === 0 ? 0 : (page - 1) * size + 1;
  const last = Math.min(page * size, data.total);

  // changing page size keeps you roughly where you were rather than throwing
  // you back to page 1 — the row you were looking at stays on screen
  function changeSize(next: number) {
    const anchor = (page - 1) * size;
    setSize(next);
    setPage(Math.max(1, Math.floor(anchor / next) + 1));
  }

  return (
    <div className="stack">
      <div className="meta">
        <strong>{data.total}</strong> documents
        <span className="spacer" />
        {FILTERS.map((f) => (
          <button key={f || "all"} className={`chip btn ${status === f ? "on" : ""}`}
                  onClick={() => { setStatus(f); setPage(1); }}>
            {f || "all"}
          </button>
        ))}
      </div>

      {data.items.length === 0 ? (
        <p className="muted">
          Nothing here yet. <Link to="/upload">Add a document</Link>.
        </p>
      ) : (
        <table className="docs">
          <thead>
            <tr><th>Title</th><th>Status</th><th>Type</th><th>Size</th><th>Updated</th></tr>
          </thead>
          <tbody>
            {data.items.map((d) => (
              <tr key={d.id}>
                <td><Link to={`/documents/${d.id}`}>{d.title}</Link></td>
                <td><StatusBadge status={d.status} /></td>
                <td className="muted">{d.content_type.replace("application/", "")}</td>
                <td className="muted num" title={`${d.byte_size.toLocaleString()} bytes`}>{formatBytesShort(d.byte_size)}</td>
                <td className="muted">{new Date(d.updated_at).toLocaleTimeString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="pager">
        <span className="muted">
          {data.total === 0
            ? "no documents"
            : <>Showing <strong>{first}–{last}</strong> of <strong>{data.total}</strong></>}
        </span>

        <span className="pager-nav">
          <button disabled={page <= 1} onClick={() => setPage(1)}
                  title="First page">&laquo;</button>
          <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</button>
          <span className="muted">page {page} of {pages}</span>
          <button disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</button>
          <button disabled={page >= pages} onClick={() => setPage(pages)}
                  title="Last page">&raquo;</button>
        </span>

        <label className="rows-per-page">
          Rows
          <select value={size} onChange={(e) => changeSize(Number(e.target.value))}>
            {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
      </div>
    </div>
  );
}
