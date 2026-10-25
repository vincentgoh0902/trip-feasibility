import { cn } from "@/lib/utils";

/** A section break: a strip of the teal water from under the hero island (globals.css). */
export function WaveDivider({ className }: { className?: string }) {
  return <div className={cn("wave-divider w-full", className)} aria-hidden />;
}
