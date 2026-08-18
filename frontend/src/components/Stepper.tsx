import type { ProgressStep } from "../api/types";

/** Every stage here is backed by a real signal — the committed row, the
 *  outbox row's published_at, and the document status. Nothing is a timer,
 *  so a stalled pipeline shows a stalled step rather than a spinner that
 *  eventually lies. */
export function Stepper({ steps }: { steps: ProgressStep[] }) {
  return (
    <ol className="stepper">
      {steps.map((s, i) => (
        <li key={s.key} className={s.state}>
          <span className="dot" aria-hidden>
            {s.state === "done" && "✓"}
            {s.state === "failed" && "✕"}
            {s.state === "active" && <span className="spin" />}
          </span>
          <div className="step-text">
            <span className="step-label">{s.label}</span>
            {s.detail && <span className="step-detail">{s.detail}</span>}
          </div>
          {i < steps.length - 1 && <span className="bar" aria-hidden />}
        </li>
      ))}
    </ol>
  );
}
