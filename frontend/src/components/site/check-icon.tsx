// Flat icons for the five checks, drawn in the hero illustration's palette: each shape sits on
// a darker copy of itself shifted down, the same "side face" as the cards and buttons.
type IconProps = { className?: string };

function Clock() {
  return (
    <>
      <circle cx="20" cy="22" r="15" fill="var(--clay-side)" />
      <circle cx="20" cy="20" r="15" fill="var(--terracotta)" />
      <circle cx="20" cy="20" r="11" fill="var(--paper)" />
      <path d="M20 20V12.5M20 20l5 3" stroke="var(--charcoal)" strokeWidth="3" strokeLinecap="round" />
      <circle cx="20" cy="20" r="2" fill="var(--charcoal)" />
    </>
  );
}

function Calendar() {
  return (
    <>
      <rect x="6" y="11" width="28" height="25" rx="5" fill="var(--wood)" />
      <rect x="6" y="9" width="28" height="25" rx="5" fill="var(--paper)" />
      <path d="M6 14a5 5 0 0 1 5-5h18a5 5 0 0 1 5 5v3H6z" fill="var(--sage)" />
      <rect x="12" y="5" width="3.5" height="8" rx="1.75" fill="var(--charcoal)" />
      <rect x="24.5" y="5" width="3.5" height="8" rx="1.75" fill="var(--charcoal)" />
      <rect x="11" y="21" width="5" height="4" rx="1.2" fill="var(--wood-line)" />
      <rect x="17.5" y="21" width="5" height="4" rx="1.2" fill="var(--wood-line)" />
      <rect x="24" y="21" width="5" height="4" rx="1.2" fill="var(--terracotta)" />
      <rect x="11" y="27" width="5" height="4" rx="1.2" fill="var(--wood-line)" />
      <rect x="17.5" y="27" width="5" height="4" rx="1.2" fill="var(--wood-line)" />
    </>
  );
}

function Route() {
  return (
    <>
      <path
        d="M10 31c0-8 20-4 20-13"
        fill="none"
        stroke="var(--water)"
        strokeWidth="3"
        strokeLinecap="round"
        strokeDasharray="0.1 5.5"
      />
      <circle cx="10" cy="32" r="4.5" fill="var(--moss)" />
      <circle cx="10" cy="31" r="4.5" fill="var(--sage)" />
      <path d="M30 23c-4-4.5-7-7.6-7-11a7 7 0 0 1 14 0c0 3.4-3 6.5-7 11z" fill="var(--clay-side)" />
      <path d="M30 21c-4-4.5-7-7.6-7-11a7 7 0 0 1 14 0c0 3.4-3 6.5-7 11z" fill="var(--terracotta)" />
      <circle cx="30" cy="10" r="2.6" fill="var(--paper)" />
    </>
  );
}

function Footsteps() {
  return (
    <>
      <g fill="var(--moss)" transform="translate(0 1.5)">
        <ellipse cx="13" cy="15" rx="5" ry="7.5" transform="rotate(-12 13 15)" />
        <ellipse cx="14.6" cy="27" rx="3.6" ry="3" transform="rotate(-12 14.6 27)" />
        <ellipse cx="27" cy="20" rx="5" ry="7.5" transform="rotate(12 27 20)" />
        <ellipse cx="25.4" cy="32" rx="3.6" ry="3" transform="rotate(12 25.4 32)" />
      </g>
      <g fill="var(--sage)">
        <ellipse cx="13" cy="15" rx="5" ry="7.5" transform="rotate(-12 13 15)" />
        <ellipse cx="14.6" cy="27" rx="3.6" ry="3" transform="rotate(-12 14.6 27)" />
        <ellipse cx="27" cy="20" rx="5" ry="7.5" transform="rotate(12 27 20)" />
        <ellipse cx="25.4" cy="32" rx="3.6" ry="3" transform="rotate(12 25.4 32)" />
      </g>
    </>
  );
}

function Bed() {
  return (
    <>
      <rect x="7" y="30" width="3" height="5" rx="1" fill="var(--charcoal)" />
      <rect x="30" y="30" width="3" height="5" rx="1" fill="var(--charcoal)" />
      <rect x="5" y="22" width="30" height="10" rx="3" fill="var(--clay-side)" />
      <rect x="5" y="20" width="30" height="10" rx="3" fill="var(--terracotta)" />
      <rect x="5" y="9" width="6" height="21" rx="2.5" fill="var(--earth)" />
      <rect x="12.5" y="15" width="8" height="5" rx="2.5" fill="var(--paper)" />
      <rect x="19" y="15" width="16" height="7" rx="3" fill="var(--sage)" />
    </>
  );
}

const ICONS: Record<string, () => React.ReactNode> = {
  opening_hours: Clock,
  public_holidays: Calendar,
  travel_time: Route,
  daily_load: Footsteps,
  hotel_red_flags: Bed,
};

/** The icon for a check (backend check name); nothing for an unknown check. */
export function CheckIcon({ check, className }: IconProps & { check: string }) {
  const Icon = ICONS[check];
  if (!Icon) return null;
  return (
    <svg viewBox="0 0 40 40" className={className} aria-hidden focusable="false">
      <Icon />
    </svg>
  );
}
