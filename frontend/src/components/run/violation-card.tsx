import { ExternalLinkIcon } from "lucide-react";

import type { Violation } from "@/lib/api/types";
import { checkLabel, sourceLink } from "@/lib/format";
import { cn } from "@/lib/utils";

import { CheckIcon } from "@/components/site/check-icon";

import { Evidence } from "./evidence";
import { SEVERITY_STYLES, SeverityBadge } from "./severity-badge";

export function ViolationCard({ violation, unfixed }: { violation: Violation; unfixed?: boolean }) {
  const source = sourceLink(violation.source);
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
      </div>
      <p className="mt-2 text-sm leading-relaxed">{violation.message}</p>
      <details className="group mt-2">
        <summary className="cursor-pointer text-sm font-medium text-link underline-offset-4 hover:underline">
          Evidence
        </summary>
        <div className="mt-2 space-y-2 border-t pt-2">
          <Evidence violation={violation} />
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
        </div>
      </details>
      {unfixed && (
        <p className="mt-2 text-xs text-muted-foreground">No fix passed the re-check for this one.</p>
      )}
    </article>
  );
}
