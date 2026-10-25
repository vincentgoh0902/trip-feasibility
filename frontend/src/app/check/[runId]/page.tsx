import { Suspense } from "react";

import { RunView } from "@/components/run/run-view";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata = { title: "Check results" };

async function Run({ params }: { params: PageProps<"/check/[runId]">["params"] }) {
  const { runId } = await params;
  return <RunView runId={runId} />;
}

export default function CheckPage({ params }: PageProps<"/check/[runId]">) {
  return (
    <Suspense fallback={<Skeleton className="mx-auto mt-8 h-96 w-[calc(100%-2rem)] max-w-5xl" />}>
      <Run params={params} />
    </Suspense>
  );
}
