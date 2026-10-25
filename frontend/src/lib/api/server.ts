import "server-only";

import createClient from "openapi-fetch";

import { apiBaseUrl } from "@/lib/config";

import type { paths } from "./schema";

/** The API from server components: straight to FastAPI, never cached. */
export function serverApi() {
  return createClient<paths>({ baseUrl: apiBaseUrl(), cache: "no-store" });
}
