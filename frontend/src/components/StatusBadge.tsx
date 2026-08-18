import type { Status } from "../api/types";

/** PENDING is not a spinner — the document is durably stored, it is just not
 *  searchable yet. Worth showing distinctly from a failure. */
export function StatusBadge({ status }: { status: Status }) {
  return <span className={`badge ${status.toLowerCase()}`}>{status}</span>;
}
