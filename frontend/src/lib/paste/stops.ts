// The paste flow's logic: recognising a pasted sample, grouping the stops the parser read
// by day, and turning the reviewed stops into what POST /checks takes.
import type { ParsedStop, Sample, Stop } from "@/lib/api/types";

function words(text: string): string {
  return text.split(/\s+/).filter(Boolean).join(" ");
}

/** The sample this text is, ignoring whitespace (as the API does). */
export function matchSample(text: string, samples: Sample[]): Sample | undefined {
  const pasted = words(text);
  return pasted ? samples.find((s) => words(s.text) === pasted) : undefined;
}

export function byDay(stops: ParsedStop[]): [number, ParsedStop[]][] {
  const days = new Map<number, ParsedStop[]>();
  for (const s of stops) days.set(s.day, [...(days.get(s.day) ?? []), s]);
  return [...days].sort(([a], [b]) => a - b);
}

/** The reviewed stops: removed ones left out, renamed ones without their map location (the
 * API looks them up again). The parse response's own fields go as they came, so an unchanged
 * sample is the same request its prepared replies answer. */
export function stopsToCheck(stops: ParsedStop[], removed: Set<string>, renamed: Map<string, string>): Stop[] {
  return stops
    .filter((s) => !removed.has(s.id))
    .map(({ resolved: _r, outside_city: _o, reason: _why, ...stop }) => {
      const name = renamed.get(stop.id)?.trim();
      return name && name !== stop.place_name ? { ...stop, place_name: name, osm: null, lat: null, lon: null } : stop;
    });
}
