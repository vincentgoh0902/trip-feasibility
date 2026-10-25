// Names for the generated API types (schema.d.ts, from openapi.json: `npm run gen:api`), plus
// the live-feed events, which aren't in the OpenAPI schema (backend runs.py).
import type { components } from "./schema";

type Schemas = components["schemas"];

export type Stop = Schemas["Stop"];
export type RunOut = Schemas["RunOut"];
export type Violation = Schemas["ViolationOut"];
export type Fix = Schemas["FixOut"];
export type CheckResult = Schemas["CheckResultOut"];
export type Sample = Schemas["SampleOut"];
export type CheckStarted = Schemas["CheckStarted"];
export type Severity = Violation["severity"];

/** Every error response: `{"error": {"code", "message"}}`. */
export interface ApiError {
  error: { code: string; message: string };
}

/** Events on GET /checks/{id}/events, by their `type`. */
export type RunEvent =
  | { type: "step_started"; step: "geocode"; stop_ids: string[] }
  | {
      type: "step_started";
      step: "checks";
      checks: string[];
      city: string;
      stops: Stop[];
    }
  | { type: "step_started"; step: "fixes" }
  | ({ type: "tool_result"; check: string } & CheckResult)
  | { type: "violation"; violation: Violation }
  | { type: "fix"; fix: Fix }
  | { type: "done"; run: RunOut }
  | { type: "error"; code: string; message: string };

export const RUN_EVENT_TYPES = [
  "step_started",
  "tool_result",
  "violation",
  "fix",
  "done",
  "error",
] as const satisfies readonly RunEvent["type"][];
