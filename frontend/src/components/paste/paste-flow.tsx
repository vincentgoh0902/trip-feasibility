"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { browserApi, errorMessage } from "@/lib/api/browser";
import type { ParseResponse, ParsedStop, Sample } from "@/lib/api/types";
import { formatDate, stopTimes } from "@/lib/format";
import { byDay, matchSample, stopsToCheck } from "@/lib/paste/stops";

const TYPE_LABELS: Record<ParsedStop["type"], string> = {
  venue: "Visit",
  meal: "Meal",
  hotel: "Hotel",
  transfer: "Travel",
};

// Paste (text, city, start date) → review the stops the parser read → start a demo run.
export function PasteFlow({ samples }: { samples: Sample[] }) {
  const router = useRouter();
  const [text, setText] = useState("");
  const [city, setCity] = useState("");
  const [startDate, setStartDate] = useState("");
  const [recognised, setRecognised] = useState<Sample | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [parsed, setParsed] = useState<ParseResponse | null>(null);
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  const [renamed, setRenamed] = useState<Map<string, string>>(new Map());

  function changeText(value: string) {
    setText(value);
    const sample = matchSample(value, samples);
    setRecognised(sample ?? null);
    // a sample pasted into empty fields: fill in the city and date it was prepared with
    if (sample && !city && !startDate) {
      setCity(sample.city);
      setStartDate(sample.start_date);
    }
  }

  async function read(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const { data, error } = await browserApi.POST("/api/v1/demo/parse", {
      body: { text, city, start_date: startDate },
    });
    setBusy(false);
    if (error || !data) {
      setError(errorMessage(error));
      return;
    }
    setParsed(data);
    setRemoved(new Set());
    setRenamed(new Map());
  }

  async function check() {
    if (!parsed) return;
    setBusy(true);
    setError(null);
    const stops = stopsToCheck(parsed.stops, removed, renamed);
    const { data, error } = await browserApi.POST("/api/v1/checks", {
      body: { pasted: { city, stops } },
    });
    if (error || !data) {
      setError(errorMessage(error));
      setBusy(false);
      return;
    }
    router.push(`/check/${data.run_id}`);
  }

  if (parsed) {
    const kept = parsed.stops.length - removed.size;
    const edited =
      removed.size > 0 ||
      [...renamed].some(
        ([id, name]) =>
          name.trim() &&
          name.trim() !== parsed.stops.find((s) => s.id === id)?.place_name,
      );
    return (
      <section aria-labelledby="review" className="mt-8">
        <h2 id="review" className="text-2xl font-bold">
          Check what it read
        </h2>
        <p className="mt-2 leading-relaxed">
          {parsed.stops.length} stops in {parsed.city} from{" "}
          {formatDate(startDate)}. Rename or remove anything it got wrong, then
          run the checks.
        </p>
        <div className="mt-6 space-y-6">
          {byDay(parsed.stops).map(([day, stops]) => (
            <div key={day}>
              <h3 className="font-mono text-xs tracking-widest text-label uppercase">
                Day {day} · {formatDate(stops[0].date)}
              </h3>
              <ul className="mt-3 space-y-3">
                {stops.map((stop) => (
                  <ReviewStop
                    key={stop.id}
                    stop={stop}
                    removed={removed.has(stop.id)}
                    name={renamed.get(stop.id) ?? stop.place_name}
                    onRename={(name) =>
                      setRenamed(new Map(renamed).set(stop.id, name))
                    }
                    onToggle={() => {
                      const next = new Set(removed);
                      if (next.has(stop.id)) next.delete(stop.id);
                      else next.add(stop.id);
                      setRemoved(next);
                    }}
                  />
                ))}
              </ul>
            </div>
          ))}
        </div>
        {edited && (
          <p className="mt-6 text-sm leading-relaxed">
            Your changes are checked against the map and the calendar as usual.
            Suggested fixes on this demo were prepared for the samples as
            written, so an edited itinerary is checked without them.
          </p>
        )}
        {error && <ErrorAlert message={error} />}
        <div className="mt-6 flex flex-wrap gap-3">
          <Button
            size="lg"
            className="h-10 px-4"
            onClick={check}
            disabled={busy || kept === 0}
          >
            {busy
              ? "Starting…"
              : `Check ${kept} ${kept === 1 ? "stop" : "stops"}`}
          </Button>
          <Button
            variant="outline"
            size="lg"
            className="h-10 px-4"
            onClick={() => setParsed(null)}
            disabled={busy}
          >
            Back to the text
          </Button>
        </div>
      </section>
    );
  }

  return (
    <form onSubmit={read} className="mt-8 space-y-5">
      <div className="space-y-2">
        <Label htmlFor="itinerary">Itinerary</Label>
        <Textarea
          id="itinerary"
          value={text}
          onChange={(e) => changeText(e.target.value)}
          placeholder="Paste the itinerary a chatbot wrote for you"
          className="min-h-56 font-sans leading-relaxed"
          required
        />
        {recognised && (
          <p className="text-sm">
            That’s the sample “{recognised.title}”: its city and start date are
            filled in.
          </p>
        )}
      </div>
      <div className="grid gap-5 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="city">City</Label>
          <Input
            id="city"
            value={city}
            onChange={(e) => setCity(e.target.value)}
            required
            maxLength={100}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="start-date">First day of the trip</Label>
          <Input
            id="start-date"
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            required
          />
        </div>
      </div>
      {error && <ErrorAlert message={error} />}
      <Button type="submit" size="lg" className="h-10 px-4" disabled={busy}>
        {busy ? "Reading the stops…" : "Read the stops"}
      </Button>
    </form>
  );
}

function ReviewStop({
  stop,
  removed,
  name,
  onRename,
  onToggle,
}: {
  stop: ParsedStop;
  removed: boolean;
  name: string;
  onRename: (name: string) => void;
  onToggle: () => void;
}) {
  return (
    <li>
      <Card className={removed ? "opacity-60" : undefined}>
        <CardContent className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <span className="w-24 shrink-0 font-mono text-sm">
            {stopTimes(stop)}
          </span>
          <div className="order-last min-w-0 flex-1 basis-full sm:order-none sm:basis-48">
            <Input
              aria-label={`Place for ${stopTimes(stop)}`}
              value={name}
              onChange={(e) => onRename(e.target.value)}
              disabled={removed}
              maxLength={300}
              className="font-semibold"
            />
            {!stop.resolved && !removed && (
              <p className="mt-1 text-xs">
                Not found on the map: checks that need its location will skip
                it.
              </p>
            )}
          </div>
          <Badge variant="outline">{TYPE_LABELS[stop.type]}</Badge>
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto sm:ml-0"
            onClick={onToggle}
          >
            {removed ? "Put back" : "Remove"}
          </Button>
        </CardContent>
      </Card>
    </li>
  );
}

function ErrorAlert({ message }: { message: string }) {
  return (
    <Alert variant="destructive" className="mt-6">
      <AlertTitle>That didn’t work</AlertTitle>
      <AlertDescription>
        {message}{" "}
        <Link href="/demo" className="underline underline-offset-2">
          See the samples
        </Link>
      </AlertDescription>
    </Alert>
  );
}
