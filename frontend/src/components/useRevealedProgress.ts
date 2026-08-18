import { useEffect, useRef, useState } from "react";

import type { ProgressStep } from "../api/types";

/** Minimum time a stage stays visible before the next is revealed. The
 *  pipeline often finishes in under 200 ms, faster than a person can read. */
const MIN_STEP_MS = 550;

const completed = (steps: ProgressStep[]) =>
  steps.filter((s) => s.state === "done").length;

/**
 * Paces how quickly COMPLETED stages are revealed, so work that finishes in
 * milliseconds is still perceivable.
 *
 * Only for documents caught mid-flight. One that was already finished when
 * the page opened renders as finished — replaying the animation for a
 * document indexed hours ago would be theatre, and worse, its spinner would
 * suggest work still in progress.
 */
export function useRevealedProgress(steps: ProgressStep[] | undefined): ProgressStep[] {
  const [revealed, setRevealed] = useState(0);
  // null until the first payload arrives, then fixed for the lifetime of the
  // view: was this document still working when we started watching?
  const paced = useRef<boolean | null>(null);
  const timer = useRef<number | null>(null);

  const target = steps ? completed(steps) : 0;
  const failed = steps?.some((s) => s.state === "failed") ?? false;

  useEffect(() => {
    if (!steps || steps.length === 0) return;

    if (paced.current === null) {
      const settled = target === steps.length || failed;
      paced.current = !settled;
      if (settled) setRevealed(steps.length);   // already done: show it as done
    }

    if (!paced.current) { setRevealed(steps.length); return; }
    if (failed) { setRevealed(steps.length); return; }
    if (revealed >= target) return;

    timer.current = window.setTimeout(
      () => setRevealed((n) => Math.min(n + 1, target)),
      revealed === 0 ? 0 : MIN_STEP_MS,
    );
    return () => { if (timer.current) window.clearTimeout(timer.current); };
  }, [steps, target, revealed, failed]);

  if (!steps) return [];
  if (paced.current === false || failed) return steps;

  return steps.map((step, i) => {
    if (i < revealed) return step;                       // genuinely done
    if (i === revealed) return { ...step, state: "active" as const };
    return { ...step, state: "pending" as const };
  });
}
