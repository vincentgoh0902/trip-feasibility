import Image from "next/image";
import Link from "next/link";

import { CheckIcon } from "@/components/site/check-icon";
import { WaveDivider } from "@/components/site/wave-divider";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const CHECKS = [
  ["opening_hours", "Opening hours", "A visit falls outside a place’s hours, or the place no longer exists.", "OpenStreetMap"],
  ["public_holidays", "Public holidays", "A day is a public holiday that may close or crowd what you planned.", "Nager.Date"],
  ["travel_time", "Travel time", "The gap between two stops is shorter than the trip there, plus a buffer.", "OSRM routing"],
  ["daily_load", "Daily load", "A day is longer, busier or more walking than your pace allows.", "Your preferences"],
  ["hotel_red_flags", "Hotel red flags", "Reviews repeatedly mention noise, dirt, safety or a poor location.", "Booking.com reviews (Kaggle)"],
] as const;

const STEPS = [
  ["Paste", "An itinerary from a chatbot or your own notes, with the city and start date."],
  ["Confirm", "Check the stops it read from your text and fix any it got wrong."],
  ["Repair", "See each problem with its evidence, and fixes that passed every check again."],
] as const;

export default function Home() {
  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-12 sm:py-16">
      <section className="grid items-center gap-8 md:grid-cols-[1fr_1.05fr] md:gap-10">
        <div>
          <p className="font-mono text-xs tracking-widest text-label uppercase">Trip feasibility checker</p>
          <h1 className="mt-3 text-4xl leading-[1.1] font-extrabold sm:text-5xl">
            Will your itinerary survive the trip?
          </h1>
          <p className="mt-5 text-lg leading-relaxed">
            Plans that look fine on paper fall apart on the day: a museum closed on Tuesdays, a 20-minute
            transfer squeezed into 10. This checks every stop against real data, shows you the evidence, and
            suggests fixes it has already re-checked.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/demo" className={cn(buttonVariants({ size: "lg" }), "h-12 px-6 text-base")}>
              Try a sample itinerary
            </Link>
          </div>
        </div>
        <Image
          src="/hero.webp"
          alt=""
          width={1312}
          height={1199}
          preload
          sizes="(min-width: 64rem) 32rem, (min-width: 48rem) 50vw, 100vw"
          className="mx-auto w-full max-w-md animate-float motion-reduce:animate-none md:max-w-none"
        />
      </section>

      <WaveDivider className="mt-16 sm:mt-20" />

      <section aria-labelledby="checks" className="mt-14 sm:mt-16">
        <h2 id="checks" className="text-3xl font-bold">
          Five checks, each with its source
        </h2>
        <ul className="mt-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {CHECKS.map(([check, name, what, source]) => (
            <li key={check} className="flex gap-4 rounded-2xl bg-card p-5 card-raised">
              <CheckIcon check={check} className="size-11 shrink-0" />
              <div>
                <h3 className="text-lg font-bold">{name}</h3>
                <p className="mt-1 text-sm leading-relaxed">{what}</p>
                <p className="mt-3 font-mono text-xs text-label">{source}</p>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <WaveDivider className="mt-16 sm:mt-20" />

      <section aria-labelledby="how" className="mt-14 sm:mt-16">
        <h2 id="how" className="text-3xl font-bold">
          How it works
        </h2>
        <ol className="mt-8 grid gap-6 sm:grid-cols-3">
          {STEPS.map(([name, what], i) => (
            <li key={name} className="flex gap-4">
              <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-secondary font-heading text-lg font-extrabold text-secondary-foreground shadow-[0_4px_0_0_var(--secondary-side)]">
                {i + 1}
              </span>
              <div>
                <h3 className="text-xl font-bold">{name}</h3>
                <p className="mt-1 text-sm leading-relaxed">{what}</p>
              </div>
            </li>
          ))}
        </ol>
        <p className="mt-10 max-w-2xl text-sm leading-relaxed text-muted-foreground">
          An AI model reads your text and proposes fixes. The checks themselves are code working on real
          data, and a fix is only shown once every check has run again on the changed plan.
        </p>
      </section>
    </div>
  );
}
