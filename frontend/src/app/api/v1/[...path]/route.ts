// Every browser call to the API comes through here (ARCHITECTURE 6c): the browser never talks
// to FastAPI or holds a token. Once sign-in exists, this is where the per-call token is added.
import type { NextRequest } from "next/server";

import { apiBaseUrl } from "@/lib/config";

// Request headers passed on; cookies and everything else stay on this server.
const REQUEST_HEADERS = ["accept", "content-type", "last-event-id", "x-forwarded-for"];
const RESPONSE_HEADERS = ["content-type", "cache-control", "retry-after", "x-accel-buffering"];

function error(status: number, code: string, message: string): Response {
  return Response.json({ error: { code, message } }, { status });
}

async function proxy(request: NextRequest, ctx: RouteContext<"/api/v1/[...path]">) {
  const { path } = await ctx.params;
  if (path.some((p) => p === "." || p === "..")) {
    return error(404, "not_found", "Not found");
  }
  const url = new URL(`/api/v1/${path.map(encodeURIComponent).join("/")}`, apiBaseUrl());
  url.search = request.nextUrl.search;

  const headers = new Headers();
  for (const name of REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      signal: request.signal, // a closed tab closes the live feed upstream too
      redirect: "manual",
      cache: "no-store",
    });
  } catch {
    if (request.signal.aborted) return new Response(null, { status: 499 });
    return error(502, "api_unreachable", "The checker isn't reachable right now. Try again in a minute.");
  }

  const out = new Headers();
  for (const name of RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  if (out.get("content-type")?.startsWith("text/event-stream")) {
    // no-transform: compression would buffer the live feed
    out.set("cache-control", "no-cache, no-transform");
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export { proxy as DELETE, proxy as GET, proxy as PATCH, proxy as POST, proxy as PUT };
