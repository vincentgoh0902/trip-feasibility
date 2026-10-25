"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { browserApi, errorMessage } from "@/lib/api/browser";

export function StartDemoButton({ sampleId }: { sampleId: string }) {
  const router = useRouter();
  const [starting, setStarting] = useState(false);

  async function start() {
    setStarting(true);
    const { data, error } = await browserApi.POST("/api/v1/checks", { body: { sample_id: sampleId } });
    if (error || !data) {
      toast.error(errorMessage(error));
      setStarting(false);
      return;
    }
    router.push(`/check/${data.run_id}`);
  }

  return (
    <Button size="lg" className="h-10 px-4" onClick={start} disabled={starting}>
      {starting ? "Starting…" : "Check this itinerary"}
    </Button>
  );
}
