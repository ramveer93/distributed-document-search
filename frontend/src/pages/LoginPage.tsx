import { useState } from "react";

import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";

const DEMO = [
  { email: "alice@acme.com", tenant: "acme", note: "600 req/min" },
  { email: "bob@globex.com", tenant: "globex", note: "300 req/min" },
  { email: "carol@initech.com", tenant: "initech", note: "suspended — expect 403" },
];

export function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("alice@acme.com");
  const [password, setPassword] = useState("demo");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(email, password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="centred">
      <form className="card login" onSubmit={submit}>
        <h1>Deeprunner</h1>
        <p className="muted">Distributed document search</p>

        <label>
          Email
          <input value={email} onChange={(e) => setEmail(e.target.value)}
                 autoComplete="username" />
        </label>
        <label>
          Password
          <input type="password" value={password} autoComplete="current-password"
                 onChange={(e) => setPassword(e.target.value)} />
        </label>

        {error && <p className="error">{error}</p>}
        <button disabled={busy}>{busy ? "signing in…" : "Sign in"}</button>

        <div className="demo">
          <span className="muted">Demo accounts — password <code>demo</code></span>
          {DEMO.map((d) => (
            <button key={d.email} type="button" className="link"
                    onClick={() => { setEmail(d.email); setPassword("demo"); }}>
              {d.email} <span className="muted">· {d.note}</span>
            </button>
          ))}
        </div>
      </form>
    </div>
  );
}
