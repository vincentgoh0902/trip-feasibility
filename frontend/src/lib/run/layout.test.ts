import { describe, expect, it } from "vitest";

import type { Stop, Violation } from "@/lib/api/types";

import { groupDays, placeViolations } from "./layout";

function stop(id: string, day: number): Stop {
  return { id, day, date: `2026-12-0${7 + day}`, place_name: id, type: "venue", area: false };
}

function violation(id: string, check: string, stopIds: string[], severity: Violation["severity"] = "warning"): Violation {
  return { id, check_name: check, severity, stop_ids: stopIds, message: id, evidence: {}, source: "" };
}

const stops = [stop("s1", 1), stop("s2", 1), stop("s3", 2)];

describe("groupDays", () => {
  it("groups stops by day, in order", () => {
    expect(groupDays([stops[2], stops[0], stops[1]]).map((d) => [d.day, d.stops.map((s) => s.id)])).toEqual([
      [1, ["s1", "s2"]],
      [2, ["s3"]],
    ]);
  });
});

describe("placeViolations", () => {
  it("puts one-stop problems on the stop, legs after their first stop, the rest on the day", () => {
    const p = placeViolations(stops, [
      violation("closed", "opening_hours", ["s1"]),
      violation("tight", "travel_time", ["s1", "s2"]),
      violation("long day", "daily_load", ["s1", "s2"]),
      violation("noisy", "hotel_red_flags", ["s2", "s3"]),
      violation("ghost", "opening_hours", ["s9"]),
    ]);
    expect(p.onStop.get("s1")?.map((v) => v.id)).toEqual(["closed"]);
    expect(p.afterStop.get("s1")?.map((v) => v.id)).toEqual(["tight"]);
    expect(p.onDay.get(1)?.map((v) => v.id)).toEqual(["long day"]);
    expect(p.onStop.get("s2")?.map((v) => v.id)).toEqual(["noisy"]);
    expect(p.elsewhere.map((v) => v.id)).toEqual(["ghost"]);
  });

  it("lists blockers before warnings", () => {
    const p = placeViolations(stops, [
      violation("w", "opening_hours", ["s3"], "warning"),
      violation("b", "public_holidays", ["s3"], "blocker"),
    ]);
    expect(p.onStop.get("s3")?.map((v) => v.id)).toEqual(["b", "w"]);
  });
});
