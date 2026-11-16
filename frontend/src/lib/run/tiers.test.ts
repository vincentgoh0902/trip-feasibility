import { describe, expect, it } from "vitest";

import type { Violation } from "@/lib/api/types";

import { opinionTiers } from "./tiers";

function violation(
  id: string,
  check: string,
  stopIds: string[],
  severity: Violation["severity"] = "warning",
  agreement?: string,
): Violation {
  return {
    id,
    check_name: check,
    severity,
    stop_ids: stopIds,
    message: id,
    evidence: agreement ? { agreement } : {},
    source: "",
  };
}

describe("opinionTiers", () => {
  it("folds agreeing and disagreeing notes into the opening-hours finding they're about", () => {
    const t = opinionTiers([
      violation("closed", "opening_hours", ["s1"], "blocker"),
      violation("agrees", "second_opinion", ["s1"], "info", "confirms"),
      violation("special", "closures", ["s2"]),
      violation("open", "second_opinion", ["s2"], "info", "disputes"),
      violation("only", "second_opinion", ["s3"], "warning", "only_second_opinion"),
      violation("tight", "travel_time", ["s1", "s2"]),
    ]);
    expect(t.byViolation.get("closed")).toMatchObject({ tier: "confirmed", note: { id: "agrees" } });
    expect(t.byViolation.get("special")).toMatchObject({ tier: "disputed", note: { id: "open" } });
    expect(t.byViolation.get("only")).toEqual({ tier: "unconfirmed", note: null });
    expect(t.byViolation.has("tight")).toBe(false);
    expect([...t.folded].sort()).toEqual(["agrees", "open"]);
  });

  it("gives nothing a tier without a second opinion", () => {
    const t = opinionTiers([violation("closed", "opening_hours", ["s1"], "blocker")]);
    expect(t.byViolation.size).toBe(0);
    expect(t.folded.size).toBe(0);
  });

  it("leaves a note on its own when no finding is there to hold it", () => {
    const t = opinionTiers([violation("agrees", "second_opinion", ["s9"], "info", "confirms")]);
    expect(t.folded.size).toBe(0);
  });
});
