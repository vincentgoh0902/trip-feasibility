// The itinerary day by day, with each problem where it happens (lib/run/layout.ts).
import { BedDoubleIcon, ExternalLinkIcon, FootprintsIcon, LandmarkIcon, UtensilsIcon } from "lucide-react";

import type { CheckResult, Stop, Violation } from "@/lib/api/types";
import { checkLabel, formatDate, stopTimes } from "@/lib/format";
import { groupDays, placeViolations } from "@/lib/run/layout";

import { ViolationCard } from "./violation-card";

const STOP_ICONS = {
  venue: LandmarkIcon,
  meal: UtensilsIcon,
  hotel: BedDoubleIcon,
  transfer: FootprintsIcon,
} satisfies Record<Stop["type"], unknown>;

function Problems({ violations, unfixed }: { violations: Violation[] | undefined; unfixed: Set<string> }) {
  if (!violations?.length) return null;
  return (
    <div className="mt-2 space-y-2">
      {violations.map((v) => (
        <ViolationCard key={v.id} violation={v} unfixed={unfixed.has(v.id)} />
      ))}
    </div>
  );
}

export function Timeline({
  stops,
  violations,
  results,
  unfixed,
}: {
  stops: Stop[];
  violations: Violation[];
  results: Record<string, CheckResult>;
  unfixed: Set<string>;
}) {
  const placed = placeViolations(stops, violations);
  // "couldn't check" notes per stop, e.g. no opening hours in OpenStreetMap
  const unknown = new Map<string, string[]>();
  for (const [check, result] of Object.entries(results)) {
    for (const u of result.unknown) {
      const notes = unknown.get(u.stop_id) ?? [];
      unknown.set(u.stop_id, [...notes, `${checkLabel(check)}: ${u.reason}`]);
    }
  }

  return (
    <div className="space-y-8">
      {groupDays(stops).map((day) => (
        <section key={day.day} aria-labelledby={`day-${day.day}`}>
          <h3 id={`day-${day.day}`} className="text-xl font-bold">
            Day {day.day} <span className="text-base font-semibold text-muted-foreground">· {formatDate(day.date)}</span>
          </h3>
          <Problems violations={placed.onDay.get(day.day)} unfixed={unfixed} />
          <ol className="mt-3 border-l-2 border-border">
            {day.stops.map((stop) => {
              const Icon = STOP_ICONS[stop.type];
              return (
                <li key={stop.id} className="relative pb-4 pl-5 last:pb-0">
                  <span className="absolute top-1.5 -left-[7px] size-3 rounded-full border-2 border-background bg-sage" aria-hidden />
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span className="w-24 shrink-0 font-mono text-xs text-muted-foreground">{stopTimes(stop)}</span>
                    <span className="flex min-w-0 items-center gap-1.5 font-medium">
                      <Icon className="size-4 shrink-0 text-moss" aria-label={stop.type} />
                      {stop.place_name}
                    </span>
                    {stop.osm ? (
                      <a
                        href={`https://www.openstreetmap.org/${stop.osm.type}/${stop.osm.id}`}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-xs text-muted-foreground underline underline-offset-2"
                      >
                        map <ExternalLinkIcon className="size-3" aria-hidden />
                      </a>
                    ) : (
                      <span className="text-xs text-muted-foreground">not found on the map</span>
                    )}
                  </div>
                  {unknown.get(stop.id)?.map((note) => (
                    <p key={note} className="mt-1 text-xs text-muted-foreground">
                      Couldn’t check · {note}
                    </p>
                  ))}
                  <Problems violations={placed.onStop.get(stop.id)} unfixed={unfixed} />
                  <Problems violations={placed.afterStop.get(stop.id)} unfixed={unfixed} />
                </li>
              );
            })}
          </ol>
        </section>
      ))}
      {placed.elsewhere.length > 0 && <Problems violations={placed.elsewhere} unfixed={unfixed} />}
    </div>
  );
}
