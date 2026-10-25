import createClient from "openapi-fetch";

import type { ApiError } from "./types";
import type { paths } from "./schema";

/** The API from the browser: same origin, through the proxy in app/api/v1. */
export const browserApi = createClient<paths>({ baseUrl: "" });

/** A message for any failed call: the API's own, or a generic one. */
export function errorMessage(error: unknown): string {
  const e = (error as Partial<ApiError> | undefined)?.error;
  return e?.message ?? "Something went wrong. Try again in a minute.";
}
