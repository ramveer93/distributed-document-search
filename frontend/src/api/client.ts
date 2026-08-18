import type {
  Accepted, DocumentDetail, DocumentListResponse, Problem, SearchResponse,
  TokenResponse,
} from "./types";

const BASE = "/api";
const TOKEN_KEY = "deeprunner.token";

export class ApiError extends Error {
  constructor(readonly problem: Problem, readonly retryAfter?: number) {
    super(problem.detail ?? problem.title);
  }
}

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

/** One session id per browser session, echoed on every call so the backend
 *  can correlate a user's requests the way its logs expect. */
const sessionId = (() => {
  const key = "deeprunner.session";
  let id = sessionStorage.getItem(key);
  if (!id) {
    id = `s-${crypto.randomUUID().slice(0, 12)}`;
    sessionStorage.setItem(key, id);
  }
  return id;
})();

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = tokenStore.get();
  const headers = new Headers(init.headers);
  // the tenant is NEVER sent — the server reads it from the token claim, so
  // there is nothing here a client could tamper with
  if (token) headers.set("Authorization", `Bearer ${token}`);
  headers.set("X-Session-Id", sessionId);

  const res = await fetch(`${BASE}${path}`, { ...init, headers });

  if (res.status === 204) return undefined as T;
  if (res.ok) return (await res.json()) as T;

  let problem: Problem;
  try {
    problem = (await res.json()) as Problem;
  } catch {
    problem = { type: "/errors/unknown", title: res.statusText, status: res.status };
  }
  if (res.status === 401) tokenStore.clear();
  const retryAfter = res.headers.get("Retry-After");
  throw new ApiError(problem, retryAfter ? Number(retryAfter) : undefined);
}

export const api = {
  login: (email: string, password: string) =>
    request<TokenResponse>("/auth/token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    }),

  search: (q: string, page = 1, size = 20, facets = ["dept"]) =>
    request<SearchResponse>(
      `/search?q=${encodeURIComponent(q)}&page=${page}&size=${size}` +
        `&facets=${facets.join(",")}`,
    ),

  listDocuments: (page = 1, size = 25, status?: string) =>
    request<DocumentListResponse>(
      `/documents?page=${page}&size=${size}` + (status ? `&status=${status}` : ""),
    ),

  getDocument: (id: string) => request<DocumentDetail>(`/documents/${id}`),

  indexText: (title: string, body: string, metadata: Record<string, unknown>) =>
    request<Accepted>("/documents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, body, metadata }),
    }),

  uploadFile: (file: File, title: string, metadata: Record<string, unknown>) => {
    const form = new FormData();
    form.append("file", file);
    if (title) form.append("title", title);
    form.append("metadata", JSON.stringify(metadata));
    // no Content-Type header: the browser must set the multipart boundary
    return request<Accepted>("/documents", { method: "POST", body: form });
  },

  remove: (id: string) => request<void>(`/documents/${id}`, { method: "DELETE" }),

  /** A browser cannot put a bearer token on an <a href> navigation, so we
   *  ask for the presigned URL as JSON and navigate to that instead. S3
   *  needs no header of ours, and the bytes never touch the API. */
  downloadUrl: (id: string) =>
    request<{ url: string; expires_in: number }>(`/documents/${id}/raw`, {
      headers: { Accept: "application/json" },
    }),
};
