import { describe, expect, it } from "vitest";

import type { CheckResult, Stop } from "@/lib/api/types";

import { unknownNotes, unverifiedGroups } from "./unknown";

function result(unknown: [string, string][]): CheckResult {
  return {
    status: "ok",
    violations: 0,
    unknown: unknown.map(([stop_id, reason]) => ({ stop_id, reason })),
    checked_stop_ids: [],
    notes: [],
    error: null,
  };
}

describe("unknownNotes", () => {
  it("says a reason shared by three or more stops once, naming them; the rest under their stops", () => {
    const stops = ["Louvre", "Orsay", "Eiffel Tower", "Café"].map((n, i) => ({ id: `s${i + 1}`, place_name: n })) as Stop[];
    const notes = unknownNotes(
      {
        closures: result([["s1", "no web searches"], ["s2", "no web searches"], ["s3", "no web searches"]]),
        opening_hours: result([["s2", "no opening_hours in OSM"], ["s4", "no opening_hours in OSM"]]),
      },
      stops,
    );
    expect(notes.shared).toEqual(["Closures for Louvre, Orsay, Eiffel Tower: no web searches"]);
    expect(notes.perStop.get("s2")).toEqual(["Opening hours: no opening_hours in OSM"]);
    expect(notes.perStop.get("s4")).toEqual(["Opening hours: no opening_hours in OSM"]);
    expect(notes.perStop.has("s1")).toBe(false);
  });
});

describe("unverifiedGroups", () => {
  it("groups a fix's unverified stops by check and reason, with place names", () => {
    const stops = [
      { id: "s1", place_name: "Louvre" },
      { id: "s3", place_name: "Orsay" },
    ] as Stop[];
    expect(
      unverifiedGroups(
        ["s1 (closures: not verified: no new web searches)", "s3 (closures: not verified: no new web searches)",
         "s1 (opening_hours: no opening_hours in OSM)"],
        stops,
      ),
    ).toEqual([
      "Closures for Louvre, Orsay: not verified: no new web searches",
      "Opening hours for Louvre: no opening_hours in OSM",
    ]);
  });
});
