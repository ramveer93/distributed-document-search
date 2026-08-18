export type Status = "PENDING" | "LIVE" | "FAILED" | "DELETED";

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  tenant: string;
}

export interface Accepted {
  id: string;
  tenant: string;
  status: Status;
  version: number;
}

export type StepState = "done" | "active" | "pending" | "failed";

export interface ProgressStep {
  key: string;
  label: string;
  state: StepState;
  detail?: string;
}

export interface DocumentSummary {
  id: string;
  title: string;
  status: Status;
  content_type: string;
  byte_size: number;
  metadata: Record<string, unknown>;
  updated_at: string;
}

export interface DocumentListResponse {
  total: number;
  page: number;
  size: number;
  items: DocumentSummary[];
}

export interface DocumentDetail {
  id: string;
  tenant: string;
  title: string;
  status: Status;
  version: number;
  content_type: string;
  byte_size: number;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  failure_reason?: string;
  body?: string;
  links?: { raw: string };
  progress?: ProgressStep[];
}

export interface Hit {
  id: string;
  score: number;
  title: string;
  snippet?: string;
  metadata: Record<string, unknown>;
}

export interface SearchResponse {
  query: string;
  tenant: string;
  total: { value: number; relation: "eq" | "gte" };
  page: number;
  size: number;
  took_ms: number;
  cache: "HIT" | "MISS";
  hits: Hit[];
  facets: Record<string, { value: string; count: number }[]>;
}

/** RFC 7807. Every error the API returns has this shape. */
export interface Problem {
  type: string;
  title: string;
  status: number;
  detail?: string;
  trace_id?: string;
}
