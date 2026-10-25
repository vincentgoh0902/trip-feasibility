// The live checklist (PRD: "progress, not spinners"): each step and check ticks off as its
// event arrives.
import { CircleAlertIcon, CircleCheckIcon, CircleDashedIcon, LoaderCircleIcon } from "lucide-react";

import { checkLabel } from "@/lib/format";
import type { RunState } from "@/lib/run/state";

type ItemState = "waiting" | "running" | "done" | "problem";

const ICONS = {
  waiting: <CircleDashedIcon className="size-4 text-sage" aria-hidden />,
  running: <LoaderCircleIcon className="size-4 animate-spin text-moss motion-reduce:animate-none" aria-hidden />,
  done: <CircleCheckIcon className="size-4 text-moss" aria-hidden />,
  problem: <CircleAlertIcon className="size-4 text-warning-ink" aria-hidden />,
};

function Item({ state, label, detail }: { state: ItemState; label: string; detail?: string }) {
  return (
    <li className="flex items-start gap-2 py-1">
      <span className="mt-0.5">{ICONS[state]}</span>
      <span className="text-sm">
        {label}
        {detail && <span className="text-muted-foreground"> · {detail}</span>}
      </span>
    </li>
  );
}

export function Progress({ state }: { state: RunState }) {
  const finished = state.phase === "done" || state.phase === "error";
  const checksStarted = state.checks.length > 0;
  return (
    <ul aria-label="Progress">
      <Item
        state={checksStarted || finished ? "done" : state.phase === "connecting" ? "waiting" : "running"}
        label="Finding the places on the map"
      />
      {state.checks.map((name) => {
        const r = state.results[name];
        const itemState: ItemState = r
          ? r.status === "unavailable"
            ? "problem"
            : "done"
          : finished
            ? "waiting"
            : "running";
        const detail = r
          ? r.status === "unavailable"
            ? "unavailable right now"
            : r.violations === 0
              ? "no problems"
              : `${r.violations} problem${r.violations === 1 ? "" : "s"}`
          : undefined;
        return <Item key={name} state={itemState} label={checkLabel(name)} detail={detail} />;
      })}
      {state.fixesStarted && (
        <Item
          state={state.phase === "done" ? "done" : state.phase === "fixes" ? "running" : "waiting"}
          label="Proposing fixes and re-checking them"
          detail={state.phase === "done" ? `${state.fixes.length} passed` : undefined}
        />
      )}
    </ul>
  );
}
