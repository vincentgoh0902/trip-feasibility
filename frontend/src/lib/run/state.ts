// What the results page knows about a run, built up from its live-feed events. A finished run
// (or a saved one, whose feed is a single `done` event) is taken whole from `done`.
import type { CheckResult, Fix, RunEvent, RunOut, Stop, Violation } from "@/lib/api/types";

export type Phase = "connecting" | "geocode" | "checks" | "fixes" | "done" | "error";

export interface RunState {
  phase: Phase;
  city: string | null;
  stops: Stop[];
  checks: string[]; // the enabled checks, in the API's order
  results: Record<string, CheckResult>;
  violations: Violation[];
  fixes: Fix[]; // valid fixes only
  fixesStarted: boolean; // only when the checks found a warning or blocker and fixes are on
  run: RunOut | null; // the final result
  error: { code: string; message: string } | null;
}

export const initialRunState: RunState = {
  phase: "connecting",
  city: null,
  stops: [],
  checks: [],
  results: {},
  violations: [],
  fixes: [],
  fixesStarted: false,
  run: null,
  error: null,
};

export type RunAction = RunEvent | { type: "reset" };

export function runReducer(state: RunState, event: RunAction): RunState {
  switch (event.type) {
    case "reset":
      return initialRunState;
    case "step_started":
      if (event.step === "geocode") return { ...state, phase: "geocode" };
      if (event.step === "fixes") return { ...state, phase: "fixes", fixesStarted: true };
      return {
        ...state,
        phase: "checks",
        checks: event.checks,
        city: event.city,
        stops: event.stops,
      };
    case "tool_result": {
      const { type: _, check, ...result } = event;
      return { ...state, results: { ...state.results, [check]: result } };
    }
    case "violation":
      if (state.violations.some((v) => v.id === event.violation.id)) return state;
      return { ...state, violations: [...state.violations, event.violation] };
    case "fix":
      if (state.fixes.some((f) => f.id === event.fix.id)) return state;
      return { ...state, fixes: [...state.fixes, event.fix] };
    case "done": {
      const { run } = event;
      return {
        ...state,
        phase: "done",
        city: run.city ?? state.city,
        stops: run.stops,
        checks: state.checks.length ? state.checks : Object.keys(run.results),
        results: run.results,
        violations: run.violations,
        fixes: run.fixes,
        fixesStarted: state.fixesStarted || run.fixes.length > 0 || run.unfixed.length > 0,
        run,
        error: null,
      };
    }
    case "error":
      return { ...state, phase: "error", error: { code: event.code, message: event.message } };
  }
}
