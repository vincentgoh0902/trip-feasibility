export function SiteFooter() {
  return (
    <footer className="border-t">
      <div className="mx-auto flex w-full max-w-5xl flex-wrap justify-between gap-2 px-4 py-6 text-xs text-muted-foreground">
        <p>Built by Vincent Goh</p>
        <a href="https://github.com/vincentgoh0902/trip-feasibility" className="underline underline-offset-2">
          Source on GitHub
        </a>
      </div>
    </footer>
  );
}
