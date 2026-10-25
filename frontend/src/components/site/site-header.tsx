import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";

export function SiteHeader() {
  return (
    <header className="border-b bg-card">
      <div className="mx-auto flex h-16 w-full max-w-5xl items-center justify-between gap-4 px-4">
        <Link href="/" className="font-heading text-xl font-extrabold text-heading">
          Trip feasibility
        </Link>
        <nav aria-label="Main" className="flex items-center gap-5 text-sm">
          <Link href="/demo" className={buttonVariants({ variant: "secondary" })}>
            Try a sample
          </Link>
        </nav>
      </div>
    </header>
  );
}
