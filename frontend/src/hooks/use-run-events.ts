"use client";

import { useEffect, useReducer } from "react";

import { RUN_EVENT_TYPES, type ApiError, type RunEvent } from "@/lib/api/types";
import { initialRunState, runReducer, type RunState } from "@/lib/run/state";

/**
 * Follows a run's live feed. The API replays every event from the start, and after a dropped
 * connection the browser reconnects with Last-Event-ID, so nothing is missed or repeated.
 */
export function useRunEvents(runId: string): RunState {
  const [state, dispatch] = useReducer(runReducer, initialRunState);

  useEffect(() => {
    dispatch({ type: "reset" });
    const source = new EventSource(`/api/v1/checks/${encodeURIComponent(runId)}/events`);

    function onEvent(message: MessageEvent<string>) {
      const event = JSON.parse(message.data) as RunEvent;
      dispatch(event);
      if (event.type === "done" || event.type === "error") source.close();
    }

    // An HTTP error (an unknown or expired run) closes the feed for good: ask why.
    async function onClosed() {
      const response = await fetch(`/api/v1/checks/${encodeURIComponent(runId)}`).catch(() => null);
      const body = (await response?.json().catch(() => null)) as Partial<ApiError> | null;
      dispatch({
        type: "error",
        code: body?.error?.code ?? "unreachable",
        message: body?.error?.message ?? "Lost the connection to the checker.",
      });
    }

    for (const type of RUN_EVENT_TYPES) {
      source.addEventListener(type, (e) => {
        // "error" is also how EventSource reports a broken connection, with no data
        if (e instanceof MessageEvent && typeof e.data === "string") onEvent(e);
        else if (source.readyState === EventSource.CLOSED) void onClosed();
      });
    }
    return () => source.close();
  }, [runId]);

  return state;
}
