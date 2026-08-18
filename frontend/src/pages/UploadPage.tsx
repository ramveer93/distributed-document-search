import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, api } from "../api/client";

type Mode = "file" | "text";

export function UploadPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("file");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [dept, setDept] = useState("ops");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null); setBusy(true);
    try {
      const metadata = { dept };
      const res = mode === "file"
        ? await api.uploadFile(file!, title, metadata)
        : await api.indexText(title, body, metadata);
      // 202, not 201 — the document is stored but not yet searchable, so we
      // hand the user to the detail page where they can watch it go LIVE
      navigate(`/documents/${res.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <div className="tabs">
        <button type="button" className={mode === "file" ? "on" : ""}
                onClick={() => setMode("file")}>Upload a file</button>
        <button type="button" className={mode === "text" ? "on" : ""}
                onClick={() => setMode("text")}>Paste text</button>
      </div>

      {mode === "file" ? (
        <>
          <label>
            File
            <input type="file" accept=".pdf,.docx,.html,.htm,.txt,.md,.csv"
                   onChange={(e) => setFile(e.target.files?.[0] ?? null)} required />
          </label>
          <p className="muted">
            PDF, DOCX, HTML and plain text. The text is extracted by the indexer,
            not by the browser or the API.
          </p>
        </>
      ) : (
        <label>
          Body
          <textarea rows={10} value={body} required
                    onChange={(e) => setBody(e.target.value)}
                    placeholder="Paste the document text…" />
        </label>
      )}

      <label>
        Title <span className="muted">(optional for files — defaults to the filename)</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)}
               required={mode === "text"} />
      </label>
      <label>
        Department
        <input value={dept} onChange={(e) => setDept(e.target.value)} />
      </label>

      {error && <p className="error">{error}</p>}
      <button disabled={busy || (mode === "file" && !file)}>
        {busy ? "sending…" : "Add document"}
      </button>
    </form>
  );
}
