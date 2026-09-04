import { useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import { streamJobEvents, TERMINAL_STATES } from "../api/jobStream";
import type { JobRead } from "../api/types";

/**
 * Live extraction progress for one document (DESIGN.md §10.2, §16.x): resolves the
 * document's job, opens its SSE stream, and renders a determinate or indeterminate
 * bar from the durable snapshot. Calls `onComplete` when the job reaches a terminal
 * state so the list can refresh the row's real status.
 */
export function JobProgress({
  documentId,
  onComplete,
}: {
  documentId: string;
  onComplete: () => void;
}) {
  const [job, setJob] = useState<JobRead | null>(null);
  // Keep the latest onComplete without re-subscribing the stream on every render.
  const done = useRef(onComplete);
  done.current = onComplete;

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    (async () => {
      let initial: JobRead;
      try {
        initial = await api.getDocumentJob(documentId);
      } catch {
        return; // no job yet / not streamable — the list poll remains the fallback
      }
      if (cancelled) return;
      setJob(initial);
      if (TERMINAL_STATES.has(initial.status)) {
        done.current();
        return;
      }
      await streamJobEvents(
        initial.id,
        (snapshot) => {
          if (cancelled) return;
          setJob(snapshot);
          if (TERMINAL_STATES.has(snapshot.status)) done.current();
        },
        controller.signal,
      );
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [documentId]);

  if (!job || TERMINAL_STATES.has(job.status)) return null;

  const label = job.stage_label ?? job.stage ?? "Working";
  const determinate = !job.indeterminate && job.overall_fraction != null;
  const pct = determinate ? Math.round((job.overall_fraction ?? 0) * 100) : null;
  const counts =
    job.total_units != null && job.unit
      ? `${job.completed_units} / ${job.total_units} ${job.unit}`
      : null;

  return (
    <div
      className="job-progress"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct ?? undefined}
    >
      <div className="job-progress-head">
        <span className="job-stage">{label}</span>
        <span className="job-meta">{pct != null ? `${pct}%` : counts ?? "…"}</span>
      </div>
      <div className={`job-track${determinate ? "" : " indeterminate"}`}>
        <div className="job-fill" style={determinate ? { width: `${pct}%` } : undefined} />
      </div>
    </div>
  );
}
