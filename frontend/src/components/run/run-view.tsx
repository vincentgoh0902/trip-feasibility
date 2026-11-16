"use client";

import { useRunEvents } from "@/hooks/use-run-events";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { WaveDivider } from "@/components/site/wave-divider";
import { checkLabel, formatDate } from "@/lib/format";
import type { RunState } from "@/lib/run/state";
import { opinionTiers, type Tiers } from "@/lib/run/tiers";

import { FixCard } from "./fix-card";
import { Progress } from "./progress";
import { Timeline } from "./timeline";

function statusText(state: RunState): string {
  switch (state.phase) {
    case "connecting":
      return "Starting the check";
    case "geocode":
      return "Finding the places on the map";
    case "checks": {
      const left = state.checks.filter((name) => !state.results[name]);
      // closures and the second opinion search the web, so they're usually the last by a
      // while; the review runs after every check
      const tools = left.filter((name) => name !== "review");
      const web = ["closures", "second_opinion"];
      if (tools.length > 0 && tools.every((name) => web.includes(name))) {
        return tools.includes("closures")
          ? "Still checking closures…"
          : "Getting a second opinion from the web…";
      }
      if (left.length === 1 && left[0] === "review")
        return "Reviewing each day…";
      return `Checking: ${Object.keys(state.results).length} of ${state.checks.length} checks done`;
    }
    case "fixes":
      return "Proposing fixes and re-checking them";
    case "done":
      return "Check finished";
    case "error":
      return "The check stopped";
  }
}

function TierSummary({ tiers }: { tiers: Tiers }) {
  const count = (tier: string) =>
    [...tiers.byViolation.values()].filter((t) => t.tier === tier).length;
  const confirmed = count("confirmed");
  const unsure = count("unconfirmed") + count("disputed");
  if (confirmed + unsure === 0) return null;
  return (
    <p className="text-sm text-muted-foreground">
      Opening hours: {confirmed} confirmed by a web search, {unsure} worth
      checking
    </p>
  );
}

function Summary({ state, tiers }: { state: RunState; tiers: Tiers }) {
  if (state.phase !== "done") return null;
  const count = (severity: string) =>
    state.violations.filter((v) => v.severity === severity).length;
  const blockers = count("blocker");
  const warnings = count("warning");
  if (blockers + warnings === 0) {
    return <p className="text-sm">No problems found in the checks that ran.</p>;
  }
  return (
    <div className="space-y-1">
      <p className="text-sm font-semibold">
        <span className="text-blocker-ink">
          {blockers} blocker{blockers === 1 ? "" : "s"}
        </span>
        {" · "}
        <span className="text-warning-ink">
          {warnings} warning{warnings === 1 ? "" : "s"}
        </span>
      </p>
      <TierSummary tiers={tiers} />
    </div>
  );
}

function Fixes({ state }: { state: RunState }) {
  const run = state.run;
  if (!state.fixesStarted && run?.fixes_enabled !== false) return null;
  return (
    <section aria-labelledby="fixes" className="space-y-4">
      <WaveDivider className="mb-8" />
      <h2 id="fixes" className="text-2xl font-bold">
        Suggested fixes
      </h2>
      {run?.fixes_enabled !== false && (
        <p className="text-sm text-muted-foreground">
          Each fix was applied to a copy of the itinerary and every check was
          run again. Only fixes that removed their problems without adding a
          blocker are shown.
        </p>
      )}
      {run && !run.fixes_enabled && (
        <p className="text-sm">
          Fix suggestions are switched off on this server.
        </p>
      )}
      {run?.fix_error && (
        <p className="text-sm">{fixErrorText(run.fix_error)}</p>
      )}
      {state.phase === "fixes" && state.fixes.length === 0 && (
        <Skeleton className="h-24 w-full" />
      )}
      {state.phase === "done" &&
        run?.fixes_enabled &&
        state.fixes.length === 0 &&
        !run.fix_error && (
          <p className="text-sm">No fix passed the re-check.</p>
        )}
      {state.fixes.map((fix) => (
        <FixCard
          key={fix.id}
          fix={fix}
          stops={state.stops}
          violations={state.violations}
          action={
            run?.demo !== false && (
              <span className="text-xs text-muted-foreground">
                Sign in to save a fixed copy of your own trips.
              </span>
            )
          }
        />
      ))}
    </section>
  );
}

export function RunView({ runId }: { runId: string }) {
  return <RunLayout state={useRunEvents(runId)} />;
}

/** The results page for a run's state, live or finished. */
export function RunLayout({ state }: { state: RunState }) {
  const unfixed = new Set(state.run?.unfixed ?? []);
  const notes = [
    ...new Set(Object.values(state.results).flatMap((r) => r.notes)),
  ];
  const unavailable = Object.entries(state.results).filter(
    ([, r]) => r.status === "unavailable",
  );
  const dates = state.stops.map((s) => s.date).sort();
  const tiers = opinionTiers(state.violations);

  return (
    <div className="mx-auto grid w-full max-w-5xl gap-8 px-4 py-8 lg:grid-cols-[17rem_1fr]">
      <aside className="space-y-5 lg:sticky lg:top-6 lg:self-start">
        <div>
          <p className="font-mono text-xs tracking-wide text-label uppercase">
            {state.run?.demo === false
              ? "Saved check"
              : "Demo check · nothing is saved"}
          </p>
          <h1 className="mt-1 text-3xl font-extrabold">
            {state.city ?? "Your trip"}
          </h1>
          {dates.length > 0 && (
            <p className="text-sm text-muted-foreground">
              {formatDate(dates[0])}
              {dates.at(-1) !== dates[0] && ` – ${formatDate(dates.at(-1)!)}`}
            </p>
          )}
        </div>
        <p role="status" aria-live="polite" className="sr-only">
          {statusText(state)}
        </p>
        <Progress state={state} />
        <Summary state={state} tiers={tiers} />
      </aside>

      <div className="min-w-0 space-y-10">
        {state.error && (
          <Alert variant="destructive">
            <AlertTitle>The check couldn’t finish</AlertTitle>
            <AlertDescription>{state.error.message}</AlertDescription>
          </Alert>
        )}
        {unavailable.length > 0 && (
          <Alert>
            <AlertTitle>Some checks couldn’t run</AlertTitle>
            <AlertDescription>
              {unavailable.map(([name]) => checkLabel(name)).join(", ")}: a data
              source was unavailable. The other results stand.
            </AlertDescription>
          </Alert>
        )}

        <section aria-labelledby="itinerary" className="space-y-4">
          <h2 id="itinerary" className="text-2xl font-bold">
            Your itinerary
          </h2>
          {state.stops.length > 0 ? (
            <Timeline
              stops={state.stops}
              violations={state.violations}
              results={state.results}
              unfixed={unfixed}
              tiers={tiers}
            />
          ) : state.phase !== "error" ? (
            <div className="space-y-3">
              <Skeleton className="h-6 w-48" />
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : null}
        </section>

        <Fixes state={state} />

        <footer className="space-y-2 border-t pt-4 text-xs text-muted-foreground">
          {notes.map((note) => (
            <p key={note}>{note}</p>
          ))}
          <p>
            Places, opening hours and routes from{" "}
            <a
              href="https://www.openstreetmap.org/copyright"
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              © OpenStreetMap contributors
            </a>
            , routed by{" "}
            <a
              href="https://routing.openstreetmap.de"
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              FOSSGIS OSRM
            </a>
            ; public holidays from Nager.Date. Public transport times are
            estimates. Spotted a wrong place or opening time?{" "}
            <a
              href="https://www.openstreetmap.org/fixthemap"
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              Fix the map
            </a>
            .
          </p>
        </footer>
      </div>
    </div>
  );
}

function fixErrorText(error: string): string {
  // a demo run whose itinerary was edited: the model's fixes were prepared for the samples
  if (error.startsWith("not_prepared")) {
    return "Fixes on this demo were prepared for the sample itineraries as written, so this edited one is checked without them.";
  }
  return `Couldn’t propose fixes this time: ${error}`;
}
