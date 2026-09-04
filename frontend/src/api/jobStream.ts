// Server-Sent Events consumer for job progress (DESIGN.md §10.2).
//
// The browser's native EventSource cannot send an Authorization header, and our
// API authenticates every request with a bearer token — so we read the SSE
// stream over fetch() + ReadableStream instead, which lets us set both the token
// and Last-Event-ID (for resume) and abort cleanly. On an unexpected stream close
// before a terminal status we reconnect, resuming from the last event id; polling
// the job snapshot remains the ultimate fallback.

import { getToken } from "../auth/session";
import type { JobRead } from "./types";

export const TERMINAL_STATES = new Set(["succeeded", "failed", "cancelled"]);

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

interface Frame {
  id: string | null;
  data: string | null;
}

function parseFrame(block: string): Frame {
  let id: string | null = null;
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("id:")) id = line.slice(3).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    // lines starting with ":" are comments (keepalives) — ignored
  }
  return { id, data: dataLines.length ? dataLines.join("\n") : null };
}

/**
 * Stream a job's progress snapshots until a terminal status or `signal` abort.
 * Calls `onSnapshot` for every event; resolves when the stream ends.
 */
export async function streamJobEvents(
  jobId: string,
  onSnapshot: (job: JobRead) => void,
  signal: AbortSignal,
): Promise<void> {
  let lastEventId: string | null = null;

  while (!signal.aborted) {
    const headers = new Headers({ Accept: "text/event-stream" });
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (lastEventId) headers.set("Last-Event-ID", lastEventId);

    let res: Response;
    try {
      res = await fetch(`/api/v1/jobs/${jobId}/events`, { headers, signal });
    } catch {
      if (signal.aborted) return;
      await delay(2000); // network hiccup — retry
      continue;
    }
    if (res.status === 404) return; // job gone / not ours — stop for good
    if (!res.ok || !res.body) {
      await delay(2000);
      continue;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let sawTerminal = false;
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let sep: number;
        while ((sep = buffer.indexOf("\n\n")) >= 0) {
          const block = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          const { id, data } = parseFrame(block);
          if (id) lastEventId = id;
          if (data) {
            const job = JSON.parse(data) as JobRead;
            onSnapshot(job);
            if (TERMINAL_STATES.has(job.status)) sawTerminal = true;
          }
        }
      }
    } catch {
      // aborted or a read error — fall through to the reconnect/exit check
    }

    if (sawTerminal || signal.aborted) return;
    await delay(1500); // stream closed without terminal — reconnect from lastEventId
  }
}
