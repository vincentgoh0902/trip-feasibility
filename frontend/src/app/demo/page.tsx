import Link from "next/link";
import { Suspense } from "react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { serverApi } from "@/lib/api/server";
import { formatDate } from "@/lib/format";

import { CopyButton } from "./copy-button";
import { StartDemoButton } from "./start-demo-button";

export const metadata = { title: "Try a sample" };

async function Samples() {
  const { data, error } = await serverApi().GET("/api/v1/demo/samples");
  if (error || !data) {
    return (
      <p className="text-sm">
        The samples couldn’t be loaded. Try again in a minute.
      </p>
    );
  }
  return (
    <ul className="grid gap-6">
      {data.map((sample) => (
        <li key={sample.id}>
          <Card>
            <CardHeader>
              <CardTitle className="text-2xl">{sample.title}</CardTitle>
              <CardDescription>
                {sample.city} · starts {formatDate(sample.start_date)}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <pre className="max-h-72 overflow-auto rounded-xl border bg-muted p-3 font-sans text-sm leading-relaxed whitespace-pre-wrap">
                {sample.text}
              </pre>
              <div className="flex flex-wrap gap-3">
                <StartDemoButton sampleId={sample.id} />
                <CopyButton text={sample.text} />
              </div>
            </CardContent>
          </Card>
        </li>
      ))}
    </ul>
  );
}

export default function DemoPage() {
  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-10">
      <h1 className="text-4xl font-extrabold">Try a sample</h1>
      <p className="mt-3 leading-relaxed">
        Itineraries written the way a chatbot writes them, checked live against
        real data with the default settings. Nothing is saved, and you don’t
        need an account.
      </p>
      <p className="mt-3 leading-relaxed">
        Check one straight away, or copy its text and{" "}
        <Link
          href="/check"
          className="font-semibold underline underline-offset-2"
        >
          paste it into the checker
        </Link>{" "}
        to see every step: reading the stops, confirming them, then the checks.
      </p>
      <div className="mt-8">
        <Suspense fallback={<Skeleton className="h-96 w-full" />}>
          <Samples />
        </Suspense>
      </div>
    </div>
  );
}
