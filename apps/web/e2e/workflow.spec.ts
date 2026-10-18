import { expect, test } from "@playwright/test";

// The complete experiment workflow through the UI, against the real API and simulator.
test("incident → experiment → compiler check → run → results → report", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Incidents" })).toBeVisible();
  await page.getByText("Forklift FL-07 stopped short of a pedestrian").click();

  // Replay of the as-is incident is a near miss computed by the engine.
  await expect(page.getByText("As-is layout, replayed incident")).toBeVisible();
  await expect(page.locator(".instrument-head .tag.near_miss")).toBeVisible();

  await page.getByRole("tab", { name: "Design an experiment" }).click();
  await page.getByRole("button", { name: "Add change" }).click();
  await expect(page.getByRole("cell", { name: "Move staged pallet stack A 3 m west", exact: true })).toBeVisible();

  // Break the experiment: robustness claim without arrival-timing variation.
  await page.getByRole("checkbox", { name: "Vary Pedestrian arrival timing shift" }).uncheck();
  await page.getByRole("button", { name: "Check experiment" }).click();
  await expect(page.getByText("The compiler rejected this experiment")).toBeVisible();
  await expect(page.getByText("missing_stress_axis", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run experiment" })).toBeDisabled();

  await page.getByRole("button", { name: "Apply corrections" }).click();
  await page.getByRole("button", { name: "Check experiment" }).click();
  await expect(page.getByText("Experiment is valid")).toBeVisible();

  // Smaller budget keeps the test quick.
  await page.getByText("Method settings").click();
  await page.getByLabel("Runs per change and workflow").fill("16");
  await page.getByRole("button", { name: "Run experiment" }).click();

  await expect(page).toHaveURL(/\/experiments\/exp_/);
  await expect(page.getByRole("link", { name: "Open evidence report" })).toBeVisible({ timeout: 200_000 });
  await expect(page.getByRole("heading", { name: "Results" })).toBeVisible();
  await expect(page.getByRole("cell", { name: /As-is \(no intervention\)/ })).toBeVisible();

  await page.getByRole("link", { name: "Open evidence report" }).click();
  await expect(page.getByRole("heading", { name: "Findings" })).toBeVisible();
  await expect(page.getByText(/As-is layout: the replayed incident is a near miss/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Limitations" })).toBeVisible();
});

test("a stored run replays to identical hashes", async ({ page, request }) => {
  const spec = {
    title: "e2e replay",
    scenario_id: "dc-aisle4-crossing",
    arms: [{ intervention: { type: "remove_obstruction", id: "rm", label: "Remove stack A", obstruction_id: "stack-a" } }],
    stress: {
      axes: [
        { name: "forklift_speed", low: 1.8, high: 3.3, unit: "m/s" },
        { name: "pedestrian_speed", low: 0.9, high: 1.8, unit: "m/s" },
        { name: "pedestrian_start_offset", low: -6, high: 6, unit: "s" },
        { name: "reaction_time", low: 0.75, high: 1.5, unit: "s" },
      ],
    },
    budget_per_arm: 16,
    compare_conventional: false,
  };
  const created = await (await request.post("/api/v1/experiments", { data: spec })).json();
  const id = created.experiment.id as string;
  await expect
    .poll(async () => (await (await request.get(`/api/v1/experiments/${id}`)).json()).status, { timeout: 120_000 })
    .toBe("completed");
  await page.goto(`/experiments/${id}/runs/baseline/targeted_search/0`);
  await expect(page.getByRole("heading", { name: "Reproduction check" })).toBeVisible();
  await expect(page.locator(".tag.ok", { hasText: "Matches" })).toHaveCount(2);
});

test("model features degrade gracefully without a key", async ({ page }) => {
  await page.goto("/incidents/dc-aisle4-crossing");
  await page.getByRole("button", { name: "Interpret with Nemotron" }).click();
  await expect(page.getByText(/NEBIUS_API_KEY is not set/)).toBeVisible();
  await expect(page.getByText("As-is layout, replayed incident")).toBeVisible();
});
