import { ExternalLinkIcon } from "lucide-react";

import type { Violation } from "@/lib/api/types";
import { checkLabel, sourceLink } from "@/lib/format";
import type { Tier, TierInfo } from "@/lib/run/tiers";
import { cn } from "@/lib/utils";

import { CheckIcon } from "@/components/site/check-icon";

import { Evidence } from "./evidence";
import { SEVERITY_STYLES, SeverityBadge } from "./severity-badge";

const TIER_CHIPS: Record<Tier, { label: string; className: string }> = {
  confirmed: { label: "Confirmed by a web search", className: "bg-confirmed-soft text-confirmed-ink" },
  disputed: { label: "A web search disagrees", className: "border border-unconfirmed text-unconfirmed-ink" },
  unconfirmed: { label: "Worth checking · one web source", className: "border border-unconfirmed text-unconfirmed-ink" },
};

function SourceLine({ source: raw }: { source: string }) {
  const source = sourceLink(raw);
  return (
    <p className="text-xs text-muted-foreground">
      Source:{" "}
      {source.href ? (
        <a href={source.href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-label underline underline-offset-2">
          {source.label}
          <ExternalLinkIcon className="size-3" aria-hidden />
        </a>
      ) : (
        source.label
      )}
    </p>
  );
}

export function ViolationCard({
  violation,
  unfixed,
  tier,
}: {
  violation: Violation;
  unfixed?: boolean;
  tier?: TierInfo;
}) {
  const chip = tier && TIER_CHIPS[tier.tier];
  return (
    <article
      id={`violation-${violation.id}`}
      className={cn(
        "scroll-mt-20 rounded-xl bg-card p-3.5 card-raised border-l-4!",
        SEVERITY_STYLES[violation.severity].edge,
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <SeverityBadge severity={violation.severity} />
        <CheckIcon check={violation.check_name} className="size-5" />
        <span className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          {checkLabel(violation.check_name)}
        </span>
        {chip && (
          <span className={cn("rounded-md px-1.5 py-0.5 text-xs font-semibold", chip.className)}>{chip.label}</span>
        )}
      </div>
      <p className="mt-2 text-sm leading-relaxed">{violation.message}</p>
      <details className="group mt-2">
        <summary className="cursor-pointer text-sm font-medium text-link underline-offset-4 hover:underline">
          Evidence
        </summary>
        <div className="mt-2 space-y-2 border-t pt-2">
          <Evidence violation={violation} />
          <SourceLine source={violation.source} />
          {tier?.note && (
            <div className="space-y-1 border-t pt-2">
              <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Second opinion</p>
              <p className="text-sm">{tier.note.message}</p>
              <SourceLine source={tier.note.source} />
            </div>
          )}
        </div>
      </details>
      {unfixed && (
        <p className="mt-2 text-xs text-muted-foreground">No fix passed the re-check for this one.</p>
      )}
    </article>
  );
}
