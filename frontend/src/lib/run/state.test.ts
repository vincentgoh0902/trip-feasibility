import { describe, expect, it } from "vitest";

import type { RunEvent, RunOut, Stop, Violation } from "@/lib/api/types";

import { initialRunState, runReducer, type RunState } from "./state";

const stop: Stop = {
  id: "s1",
  day: 1,
  date: "2026-12-08",
  start_time: "09:00:00",
  end_time: "12:00:00",
  place_name: "Louvre Museum",
  type: "venue",
};

const violation: Violation = {
  id: "v1",
  check_name: "opening_hours",
  severity: "blocker",
  stop_ids: ["s1"],
  message: "Closed on Tuesdays",
  evidence: {},
  source: "https://www.openstreetmap.org/relation/1",
};

const result = {
  status: "ok" as const,
  violations: 1,
  unknown: [],
  checked_stop_ids: ["s1"],
  notes: [],
  error: null,
};

function play(events: RunEvent[], from: RunState = initialRunState): RunState {
  return events.reduce(runReducer, from);
}

describe("runReducer", () => {
  it("lays out the stops when the checks start, then adds results as they arrive", () => {
    const state = play([
      { type: "step_started", step: "geocode", stop_ids: ["s1"] },
      { type: "step_started", step: "checks", checks: ["opening_hours"], city: "Paris", stops: [stop] },
      { type: "tool_result", check: "opening_hours", ...result },
      { type: "violation", violation },
    ]);
    expect(state.phase).toBe("checks");
    expect(state.city).toBe("Paris");
    expect(state.stops).toEqual([stop]);
    expect(state.results.opening_hours).toEqual(result);
    expect(state.violations).toEqual([violation]);
  });

  it("ignores an event it has already seen (a replayed feed)", () => {
    const state = play([
      { type: "violation", violation },
      { type: "violation", violation },
    ]);
    expect(state.violations).toHaveLength(1);
  });

  it("takes a saved run whole from its single done event", () => {
    const run: RunOut = {
      id: "r1",
      status: "done",
      demo: false,
      version_id: "v",
      city: "Paris",
      stops: [stop],
      results: { opening_hours: result },
      violations: [violation],
      fixes: [],
      unfixed: ["v1"],
      fixes_enabled: true,
    };
    const state = play([{ type: "done", run }]);
    expect(state.phase).toBe("done");
    expect(state.checks).toEqual(["opening_hours"]);
    expect(state.fixesStarted).toBe(true); // a violation went to the fixer
    expect(state.run).toBe(run);
  });

  it("keeps what arrived before an error", () => {
    const state = play([
      { type: "violation", violation },
      { type: "error", code: "unavailable", message: "Couldn't look up the city" },
    ]);
    expect(state.phase).toBe("error");
    expect(state.violations).toHaveLength(1);
    expect(state.error?.code).toBe("unavailable");
  });
});
