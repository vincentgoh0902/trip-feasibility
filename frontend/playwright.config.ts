// End-to-end tests (e2e/): a real browser against the full stack. The API must already be
// running (`docker compose up -d --build` at the repo root, on API_INTERNAL_URL); the frontend
// is built and started here on its own port (E2E_PORT, 3100), never reusing a server, so a
// stale `next dev` on 3000 can't stand in for the current code. The demo run calls the real
// data sources and, with a key on the API, the fix model: run these by hand (../e2e/run.sh),
// not in CI.
import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.E2E_PORT ?? 3100);
const baseURL = `http://localhost:${port}`;

export default defineConfig({
  testDir: "e2e",
  timeout: 240_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL, trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "phone", use: { ...devices["Desktop Chrome"], viewport: { width: 375, height: 812 } } },
  ],
  webServer: {
    command: `npm run build && npm run start -- --port ${port}`,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 300_000,
  },
});
