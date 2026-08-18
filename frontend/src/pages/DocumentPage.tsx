import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { DocumentDetail } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import { formatBytes } from "../lib/format";
import { Stepper } from "../components/Stepper";
import { useRevealedProgress } from "../components/useRevealedProgress";

// the whole pipeline takes 1-2s, so poll faster than that or the
// intermediate stages flash past unseen
const POLL_MS = 400;

export function DocumentPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const steps = useRevealedProgress(doc?.progress);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    try {
      setDoc(await api.getDocument(id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "could not load");
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);

  // This endpoint reads Postgres, so it answers during PENDING — which is the
  // whole reason it exists. Elasticsearch would have nothing until LIVE.
  useEffect(() => {
    if (doc?.status !== "PENDING") return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [doc?.status, load]);

  function copyId() {
    void navigator.clipboard.writeText(id);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  }

  async function download() {
    try {
      const { url } = await api.downloadUrl(id);
      window.location.assign(url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "download failed");
    }
  }

  async function remove() {
    if (!confirm("Delete this document?")) return;
    try {
      await api.remove(id);
      navigate("/search");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "delete failed");
    }
  }

  if (error) return <p className="error">{error}</p>;
  if (!doc) return <p className="muted">Loading…</p>;

  return (
    <article className="card stack">
      <header className="doc-head">
        <h2>{doc.title}</h2>
        <StatusBadge status={doc.status} />
      </header>

      {steps.length > 0 && <Stepper steps={steps} />}
      {doc.status === "FAILED" && <p className="error">{doc.failure_reason}</p>}

      <dl className="facts">
        <div><dt>Size</dt><dd>{formatBytes(doc.byte_size)}</dd></div>
        <div><dt>Type</dt><dd>{doc.content_type}</dd></div>
        <div><dt>Version</dt><dd>{doc.version}</dd></div>
        <div><dt>Created</dt><dd>{new Date(doc.created_at).toLocaleString()}</dd></div>
      </dl>

      {/* the id is what appears in logs, the outbox row, the Kafka message
          key and the S3 path — worth being able to copy it */}
      <dl className="facts">
        <div className="wide">
          <dt>Document ID</dt>
          <dd className="mono id-row">
            <span>{doc.id}</span>
            <button className="link tiny" onClick={copyId}>
              {copied ? "copied" : "copy"}
            </button>
          </dd>
        </div>
      </dl>

      {Object.keys(doc.metadata).length > 0 && (
        <div className="facets">
          {Object.entries(doc.metadata).map(([k, v]) => (
            <span className="chip" key={k}>{k} · {String(v)}</span>
          ))}
        </div>
      )}

      {/* small bodies come back inline; larger ones are a link, so the
          response size never tracks the document size */}
      {doc.body
        ? <pre className="body">{doc.body}</pre>
        : doc.links && (
            <p>
              <button className="link" onClick={download}>Download original</button>
              <span className="muted"> — a 60-second presigned URL, straight from storage</span>
            </p>
          )}

      <div className="row">
        <button className="danger" onClick={remove}>Delete</button>
      </div>
    </article>
  );
}
