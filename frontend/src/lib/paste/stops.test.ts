import { describe, expect, it } from "vitest";

import type { ParsedStop, Sample } from "@/lib/api/types";

import { byDay, matchSample, stopsToCheck } from "./stops";

const SAMPLE = { id: "london", title: "London", city: "London", start_date: "2026-12-28",
  text: "**Day 1**\n- 9:30 – 11:30: Sir John Soane's Museum.\n" } as Sample; // prettier-ignore

function stop(id: string, day: number, place_name: string): ParsedStop {
  return { id, day, date: "2026-12-28", place_name, type: "venue", resolved: true, outside_city: false,
           reason: null, osm: { type: "way", id: 1 }, lat: 51.5, lon: -0.1 } as ParsedStop; // prettier-ignore
}

describe("matchSample", () => {
  it("recognises a sample whatever happened to its whitespace", () => {
    expect(matchSample("  **Day 1** - 9:30 – 11:30: Sir John Soane's Museum.", [SAMPLE])).toBe(SAMPLE);
    expect(matchSample("**Day 1** - 9:30: the British Museum", [SAMPLE])).toBeUndefined();
    expect(matchSample("   ", [SAMPLE])).toBeUndefined();
  });
});

describe("byDay", () => {
  it("groups stops by day, in day order, keeping their order within a day", () => {
    const days = byDay([stop("s1", 2, "A"), stop("s2", 1, "B"), stop("s3", 2, "C")]);
    expect(days.map(([day, stops]) => [day, stops.map((s) => s.id)])).toEqual([
      [1, ["s2"]],
      [2, ["s1", "s3"]],
    ]);
  });
});

describe("stopsToCheck", () => {
  it("drops removed stops, unlocates renamed ones and sends the rest as parsed", () => {
    const stops = [stop("s1", 1, "Soane"), stop("s2", 1, "British Museum"), stop("s3", 1, "Dishoom")];
    const out = stopsToCheck(stops, new Set(["s3"]), new Map([["s2", " Tate Modern "], ["s1", "Soane"]]));
    expect(out.map((s) => s.id)).toEqual(["s1", "s2"]);
    expect(out[0]).toEqual({ id: "s1", day: 1, date: "2026-12-28", place_name: "Soane", type: "venue",
                             osm: { type: "way", id: 1 }, lat: 51.5, lon: -0.1 }); // prettier-ignore
    expect(out[1]).toMatchObject({ place_name: "Tate Modern", osm: null, lat: null, lon: null });
  });
});
