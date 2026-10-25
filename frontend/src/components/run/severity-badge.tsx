import { Badge } from "@/components/ui/badge";
import type { Severity } from "@/lib/api/types";
import { SEVERITY_LABELS } from "@/lib/format";
import { cn } from "@/lib/utils";

export const SEVERITY_STYLES: Record<Severity, { badge: string; edge: string }> = {
  blocker: { badge: "bg-blocker-soft text-blocker-ink", edge: "border-l-blocker!" },
  warning: { badge: "bg-warning-soft text-warning-ink", edge: "border-l-warning!" },
  info: { badge: "bg-info-soft text-info-ink", edge: "border-l-info!" },
};

export function SeverityBadge({ severity, className }: { severity: Severity; className?: string }) {
  return (
    <Badge className={cn("rounded-md font-semibold", SEVERITY_STYLES[severity].badge, className)}>
      {SEVERITY_LABELS[severity]}
    </Badge>
  );
}
