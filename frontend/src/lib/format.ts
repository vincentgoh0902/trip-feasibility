import type { Severity, Stop } from "@/lib/api/types";

export const CHECK_LABELS: Record<string, string> = {
  opening_hours: "Opening hours",
  public_holidays: "Public holidays",
  travel_time: "Travel time",
  daily_load: "Daily load",
  hotel_red_flags: "Hotel red flags",
};

export const SEVERITY_LABELS: Record<Severity, string> = {
  blocker: "Blocker",
  warning: "Warning",
  info: "Info",
};

export const SEVERITY_RANK: Record<Severity, number> = { blocker: 0, warning: 1, info: 2 };

export function checkLabel(name: string): string {
  return CHECK_LABELS[name] ?? name.replaceAll("_", " ");
}

/** "09:00:00" → "09:00" */
export function formatTime(time: string | null | undefined): string | null {
  return time ? time.slice(0, 5) : null;
}

export function stopTimes(stop: Stop): string {
  const start = formatTime(stop.start_time);
  const end = formatTime(stop.end_time);
  if (start && end) return `${start}–${end}`;
  return start ?? (end ? `until ${end}` : "Any time");
}

const dateFormat = new Intl.DateTimeFormat("en-GB", {
  weekday: "short",
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC", // dates are calendar days, not instants
});

/** "2026-12-08" → "Tue 8 Dec 2026" */
export function formatDate(date: string): string {
  return dateFormat.format(new Date(`${date}T00:00:00Z`));
}

/** A violation's source: a link when it's a URL, plain text otherwise. */
export function sourceLink(source: string): { href: string | null; label: string } {
  try {
    const url = new URL(source);
    if (url.protocol === "https:" || url.protocol === "http:") {
      return { href: url.href, label: `${url.hostname}${url.pathname === "/" ? "" : url.pathname}` };
    }
  } catch {}
  return { href: null, label: source };
}
