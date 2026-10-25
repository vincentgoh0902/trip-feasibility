import { WrenchIcon } from "lucide-react";

import type { Fix, Stop, Violation } from "@/lib/api/types";
import { checkLabel, stopTimes } from "@/lib/format";

/** What a fix operation does, in words (backend agent/fixes.py: move, remove, replace, ...). */
function describe(op: Record<string, unknown>, stops: Map<string, Stop>): string {
  const name = stops.get(String(op.stop_id))?.place_name ?? "a stop";
  switch (op.op) {
    case "move":
      return `Move ${name}`;
    case "remove":
      return `Remove ${name}`;
    case "replace":
      return `Replace ${name} with ${String(op.place_name ?? "another place")}`;
    case "add_transport_mode":
      return `Allow travel by ${String(op.mode)}`;
    default:
      return String(op.op ?? "Change");
  }
}

export function FixCard({
  fix,
  stops,
  violations,
  action,
}: {
  fix: Fix;
  stops: Stop[];
  violations: Violation[];
  action: React.ReactNode;
}) {
  const byId = new Map(stops.map((s) => [s.id, s]));
  const after = new Map(fix.stops.map((s) => [s.id, s]));
  const targets = violations.filter((v) => fix.violation_ids.includes(v.id));
  return (
    <article className="rounded-xl bg-card p-3.5 card-raised">
      <div className="flex items-start gap-2">
        <WrenchIcon className="mt-0.5 size-4 shrink-0 text-moss" aria-hidden />
        <p className="text-sm leading-relaxed">{fix.description}</p>
      </div>
      <ul className="mt-2 space-y-1 pl-6 text-sm">
        {fix.operations.map((op, i) => {
          const moved = after.get(String(op.stop_id));
          return (
            <li key={i} className="list-disc marker:text-sage">
              {describe(op, byId)}
              {op.op === "move" && moved && (
                <span className="font-mono text-xs text-muted-foreground"> → day {moved.day}, {stopTimes(moved)}</span>
              )}
            </li>
          );
        })}
      </ul>
      {targets.length > 0 && (
        <p className="mt-2 text-xs text-muted-foreground">
          Solves:{" "}
          {targets.map((v, i) => (
            <span key={v.id}>
              {i > 0 && ", "}
              <a href={`#violation-${v.id}`} className="underline underline-offset-2">
                {checkLabel(v.check_name).toLowerCase()} ({v.severity})
              </a>
            </span>
          ))}
        </p>
      )}
      {fix.new_warnings.length > 0 && (
        <p className="mt-1 text-xs text-warning-ink">New warnings: {fix.new_warnings.join("; ")}</p>
      )}
      {fix.unverified.length > 0 && (
        <p className="mt-1 text-xs text-muted-foreground">Not verified: {fix.unverified.join("; ")}</p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">{action}</div>
    </article>
  );
}
