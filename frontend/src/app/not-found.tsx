import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-20">
      <h1 className="text-4xl font-extrabold">Page not found</h1>
      <p className="mt-3">
        Nothing here. <Link href="/demo" className="text-link underline underline-offset-4">Try a sample itinerary</Link>{" "}
        instead.
      </p>
    </div>
  );
}
