// The data behind a violation, per check (backend checks/*.py "evidence"). Anything a view
// doesn't recognise is shown as plain key/value pairs.
import type { Violation } from "@/lib/api/types";
import { formatDate, formatTime } from "@/lib/format";

type Data = Record<string, unknown>;

const num = (v: unknown) => (typeof v === "number" ? v : null);
const str = (v: unknown) => (typeof v === "string" ? v : null);
const obj = (v: unknown) => (v && typeof v === "object" && !Array.isArray(v) ? (v as Data) : null);

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-3 py-0.5">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 font-mono text-xs leading-5 break-words">{children}</dd>
    </div>
  );
}

function OpeningHours({ e }: { e: Data }) {
  if (e.found === false) {
    return <Row label="OpenStreetMap">The linked map object no longer exists.</Row>;
  }
  const open = Array.isArray(e.open_that_day) ? (e.open_that_day as string[]) : null;
  const start = formatTime(str(e.visit_start));
  const end = formatTime(str(e.visit_end));
  return (
    <>
      {str(e.opening_hours) && <Row label="Opening hours">{str(e.opening_hours)}</Row>}
      {open && <Row label="Open that day">{open.length ? open.join(", ") : "closed"}</Row>}
      {(start || end) && <Row label="Your visit">{`${start ?? "?"}–${end ?? "?"}`}</Row>}
      {obj(e.tags) && <Row label="Map tags">{Object.keys(obj(e.tags)!).join(", ")}</Row>}
    </>
  );
}

const MODE_LABELS: Record<string, string> = {
  walk: "On foot",
  transit: "Public transport",
  bike: "Bike",
  car: "Car",
  taxi: "Taxi",
};

function TravelTime({ e }: { e: Data }) {
  const options = Object.entries(obj(e.options) ?? {}).map(([mode, o]) => [mode, obj(o)] as const);
  return (
    <>
      <Row label="Time between stops">{`${num(e.gap_min)} min (buffer ${num(e.buffer_min)} min)`}</Row>
      {options.map(([mode, o]) =>
        o ? (
          <Row key={mode} label={MODE_LABELS[mode] ?? mode}>
            {`${Math.round(num(o.minutes) ?? 0)} min · ${num(o.km)} km`}
            {o.estimated === true && <span className="text-muted-foreground"> · estimated</span>}
          </Row>
        ) : null,
      )}
    </>
  );
}

function DailyLoad({ e }: { e: Data }) {
  const limits = obj(e.limits) ?? {};
  const pair = (value: unknown, limit: unknown, unit = "") =>
    value == null ? "unknown" : `${value}${unit} (limit ${limit}${unit})`;
  return (
    <>
      <Row label="Pace">{str(e.pace)}</Row>
      <Row label="Day length">{pair(e.day_hours, limits.day_hours, " h")}</Row>
      <Row label="Venues">{pair(e.venues, limits.venues)}</Row>
      <Row label="Walking">{pair(e.walk_km, limits.walk_km, " km")}</Row>
    </>
  );
}

function PublicHolidays({ e }: { e: Data }) {
  const counties = Array.isArray(e.counties) ? (e.counties as string[]) : null;
  return (
    <>
      <Row label="Holiday">
        {str(e.name)}
        {str(e.local_name) && str(e.local_name) !== str(e.name) && ` (${str(e.local_name)})`}
      </Row>
      {str(e.date) && <Row label="Date">{formatDate(str(e.date)!)}</Row>}
      <Row label="Where">{e.global === false && counties ? counties.join(", ") : `all of ${str(e.country_code)}`}</Row>
    </>
  );
}

function HotelRedFlags({ e }: { e: Data }) {
  const themes = Object.entries(obj(e.themes) ?? {})
    .map(([name, t]) => [name, obj(t) ?? {}] as const)
    .filter(([, t]) => t.flagged === true);
  const examples = obj(e.examples) ?? {};
  return (
    <>
      <Row label="Hotel">{str(e.hotel)}</Row>
      <Row label="Review score">{`${num(e.average_score)} / 10 · ${num(e.negative_reviews)} negative reviews`}</Row>
      {themes.map(([name, t]) => (
        <Row key={name} label={name}>
          {`${num(t.reviews)} reviews (${Math.round((num(t.share) ?? 0) * 100)}%)`}
        </Row>
      ))}
      {themes.map(([name]) =>
        (Array.isArray(examples[name]) ? (examples[name] as Data[]) : []).slice(0, 2).map((x, i) => (
          <blockquote key={`${name}-${i}`} className="my-1 border-l-2 border-border pl-3 text-xs text-muted-foreground">
            “{str(x.text)}” <span className="font-mono">({str(x.date)})</span>
          </blockquote>
        )),
      )}
    </>
  );
}

function Raw({ e }: { e: Data }) {
  return (
    <>
      {Object.entries(e).map(([key, value]) => (
        <Row key={key} label={key.replaceAll("_", " ")}>
          {typeof value === "string" ? value : JSON.stringify(value)}
        </Row>
      ))}
    </>
  );
}

const VIEWS: Record<string, (props: { e: Data }) => React.ReactNode> = {
  opening_hours: OpeningHours,
  travel_time: TravelTime,
  daily_load: DailyLoad,
  public_holidays: PublicHolidays,
  hotel_red_flags: HotelRedFlags,
};

export function Evidence({ violation }: { violation: Violation }) {
  const View = VIEWS[violation.check_name] ?? Raw;
  return (
    <dl className="text-sm">
      <View e={violation.evidence} />
    </dl>
  );
}
