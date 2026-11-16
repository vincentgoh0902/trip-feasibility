// "Couldn't check" notes (a check's `unknown` stops). A reason a check gives for several stops
// (the demo's closures: no web searches) is said once, naming them, not under each stop.
import type { CheckResult, Stop } from "@/lib/api/types";
import { checkLabel } from "@/lib/format";

export interface UnknownNotes {
  perStop: Map<string, string[]>;
  shared: string[]; // "Closures for Louvre, Orsay and Eiffel Tower: reason"
}

const SHARED_FROM = 3; // stops with the same reason

export function unknownNotes(results: Record<string, CheckResult>, stops: Stop[]): UnknownNotes {
  const names = new Map(stops.map((s) => [s.id, s.place_name]));
  const notes: UnknownNotes = { perStop: new Map(), shared: [] };
  for (const [check, result] of Object.entries(results)) {
    const byReason = new Map<string, string[]>();
    for (const u of result.unknown) byReason.set(u.reason, [...(byReason.get(u.reason) ?? []), u.stop_id]);
    for (const [reason, ids] of byReason) {
      if (ids.length >= SHARED_FROM) {
        const listed = ids.map((id) => names.get(id) ?? id);
        notes.shared.push(`${checkLabel(check)} for ${listed.join(", ")}: ${reason}`);
        continue;
      }
      for (const id of ids) {
        notes.perStop.set(id, [...(notes.perStop.get(id) ?? []), `${checkLabel(check)}: ${reason}`]);
      }
    }
  }
  return notes;
}

export function unverifiedGroups(items: string[], stops: Stop[]): string[] {
  const names = new Map(stops.map((s) => [s.id, s.place_name]));
  const groups = new Map<string, string[]>();
  for (const item of items) {
    const m = /^(\S+) \(([^:]+): (.*)\)$/.exec(item);
    const key = m ? `${checkLabel(m[2])}: ${m[3]}` : item;
    const place = m ? (names.get(m[1]) ?? m[1]) : null;
    const list = groups.get(key) ?? [];
    if (place && !list.includes(place)) list.push(place);
    groups.set(key, list);
  }
  return [...groups].map(([key, places]) => {
    if (!places.length) return key;
    const [check, ...reason] = key.split(": ");
    return `${check} for ${places.join(", ")}: ${reason.join(": ")}`;
  });
}
