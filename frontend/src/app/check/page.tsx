import Link from "next/link";
import { Suspense } from "react";

import { PasteFlow } from "@/components/paste/paste-flow";
import { Skeleton } from "@/components/ui/skeleton";
import { serverApi } from "@/lib/api/server";

export const metadata = { title: "Check an itinerary" };

async function Flow() {
  const { data } = await serverApi().GET("/api/v1/demo/samples");
  return <PasteFlow samples={data ?? []} />;
}

export default function PastePage() {
  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-10">
      <h1 className="text-4xl font-extrabold">Check an itinerary</h1>
      <p className="mt-3 leading-relaxed">
        Paste a day-by-day plan the way a chatbot wrote it. It’s read into stops
        for you to confirm, then every stop is checked against real data.
      </p>
      <p className="mt-3 rounded-xl bg-muted p-3 text-sm leading-relaxed">
        This demo checks its{" "}
        <Link
          href="/demo"
          className="font-semibold underline underline-offset-2"
        >
          sample itineraries
        </Link>{" "}
        only: copy one and paste it here. Checking your own trips isn’t open to
        visitors yet.
      </p>
      <Suspense fallback={<Skeleton className="mt-8 h-96 w-full" />}>
        <Flow />
      </Suspense>
    </div>
  );
}
