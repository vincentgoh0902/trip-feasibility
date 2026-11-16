import { expect, test, type Page } from "@playwright/test";

async function noHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

test("landing page links to the demo", async ({ page }) => {
  await page.goto("/");
  await noHorizontalScroll(page);
  await page.getByRole("link", { name: "Try a sample itinerary" }).click();
  await expect(page).toHaveURL(/\/demo$/);
  await expect(
    page.getByRole("heading", { name: "Try a sample", level: 1 }),
  ).toBeVisible();
});

test("a demo check runs to the end through the proxy", async ({
  page,
}, testInfo) => {
  // one live run is enough: the phone project only checks the layout of the finished page
  test.skip(
    testInfo.project.name === "phone",
    "the desktop project runs the live check",
  );
  await page.goto("/demo");
  await page
    .getByRole("button", { name: "Check this itinerary" })
    .first()
    .click();
  await expect(page).toHaveURL(/\/check\/[0-9a-f-]+$/);
  await expect(page.getByText("Demo check · nothing is saved")).toBeVisible();

  // the status line is the SSE-driven state machine's phase; fixes can take a while
  await expect(page.getByRole("status")).toHaveText("Check finished", {
    timeout: 200_000,
  });
  await expect(page.getByText("The check couldn’t finish")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Your itinerary" }),
  ).toBeVisible();
  await expect(
    page.getByRole("list", { name: "Progress" }).getByRole("listitem"),
  ).not.toHaveCount(0);
  await expect(page.getByRole("link", { name: "Fix the map" })).toBeVisible();
  await expect(
    page.getByRole("link", { name: "© OpenStreetMap contributors" }),
  ).toBeVisible();

  // the same finished run, read back at phone width
  await page.setViewportSize({ width: 375, height: 812 });
  await page.reload();
  await expect(page.getByRole("status")).toHaveText("Check finished");
  await noHorizontalScroll(page);
});

test("the demo page fits a phone", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "layout check at 375 px only");
  await page.goto("/demo");
  await expect(
    page.getByRole("button", { name: "Check this itinerary" }).first(),
  ).toBeVisible();
  await noHorizontalScroll(page);
});

test("a pasted sample is read, confirmed and checked, with prepared fixes", async ({
  page,
  request,
}, testInfo) => {
  // needs the prepared replies: `python -m trip_feasibility.demo.warm` once on this database
  test.skip(
    testInfo.project.name === "phone",
    "the desktop project runs the live check",
  );
  const samples = await (await request.get("/api/v1/demo/samples")).json();
  const london = samples.find(
    (s: { id: string }) => s.id === "london-bank-holiday",
  );
  await page.goto("/check");
  await noHorizontalScroll(page);
  await page.getByLabel("Itinerary").fill(london.text);
  await expect(page.getByLabel("City")).toHaveValue("London"); // a recognised sample fills these in
  await expect(page.getByLabel("First day of the trip")).toHaveValue(
    "2026-12-28",
  );
  await page.getByRole("button", { name: "Read the stops" }).click();
  await expect(
    page.getByRole("heading", { name: "Check what it read" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /^Check \d+ stops$/ }).click();
  await expect(page).toHaveURL(/\/check\/[0-9a-f-]+$/);
  await expect(page.getByRole("status")).toHaveText("Check finished", {
    timeout: 200_000,
  });
  await expect(
    page.getByRole("heading", { name: "Suggested fixes" }),
  ).toBeVisible();
  await expect(
    page.getByText("prepared for the sample itineraries"),
  ).toHaveCount(0);
});

test("text that isn't a sample is refused without reading it", async ({
  page,
}) => {
  await page.goto("/check");
  await page.getByLabel("Itinerary").fill("Day 1\n9:00 – 12:00: Louvre Museum");
  await page.getByLabel("City").fill("Paris");
  await page.getByLabel("First day of the trip").fill("2026-12-08");
  await page.getByRole("button", { name: "Read the stops" }).click();
  await expect(
    page.getByText("This demo reads its sample itineraries only"),
  ).toBeVisible();
  await noHorizontalScroll(page);
});
