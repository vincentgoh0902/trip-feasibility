import "server-only";

// Server-side settings from the environment. Empty values count as unset, as in the backend.

/** Where the Next.js server reaches FastAPI (the browser never does). */
export function apiBaseUrl(): string {
  return process.env.API_INTERNAL_URL || "http://localhost:8020";
}
