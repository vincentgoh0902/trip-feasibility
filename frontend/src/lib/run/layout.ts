// Where each violation goes on the day-by-day timeline: on one stop (a hotel's flags go on its
// first stop), on the leg between two stops (travel time), or on the whole day (daily load,
// public holidays).
import type { Stop, Violation } from "@/lib/api/types";
import { SEVERITY_RANK } from "@/lib/format";

export interface Day {
  day: number;
  date: string;
  stops: Stop[];
}

export interface Placement {
  onStop: Map<string, Violation[]>;
  afterStop: Map<string, Violation[]>; // the leg from this stop to the next
  onDay: Map<number, Violation[]>;
  elsewhere: Violation[]; // names no stop we know (shouldn't happen)
}

export function groupDays(stops: Stop[]): Day[] {
  const days = new Map<number, Day>();
  for (const stop of stops) {
    const day = days.get(stop.day) ?? { day: stop.day, date: stop.date, stops: [] };
    day.stops.push(stop);
    days.set(stop.day, day);
  }
  return [...days.values()].sort((a, b) => a.day - b.day);
}

export function bySeverity(violations: Violation[]): Violation[] {
  return [...violations].sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]);
}

function push<K>(map: Map<K, Violation[]>, key: K, v: Violation) {
  map.set(key, [...(map.get(key) ?? []), v]);
}

export function placeViolations(stops: Stop[], violations: Violation[]): Placement {
  const byId = new Map(stops.map((s) => [s.id, s]));
  const placement: Placement = {
    onStop: new Map(),
    afterStop: new Map(),
    onDay: new Map(),
    elsewhere: [],
  };
  for (const v of bySeverity(violations)) {
    const named = v.stop_ids.map((id) => byId.get(id)).filter((s) => s !== undefined);
    if (named.length === 0) placement.elsewhere.push(v);
    else if (named.length === 1 || v.check_name === "hotel_red_flags") {
      push(placement.onStop, named[0].id, v);
    }
    else if (v.check_name === "travel_time") push(placement.afterStop, named[0].id, v);
    else push(placement.onDay, named[0].day, v);
  }
  return placement;
}
