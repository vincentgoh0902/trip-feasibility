"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";

export function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      toast.error("Couldn’t copy: select the text and copy it yourself.");
    }
  }

  return (
    <Button variant="outline" size="lg" className="h-10 px-4" onClick={copy}>
      {copied ? "Copied" : "Copy the text"}
    </Button>
  );
}
