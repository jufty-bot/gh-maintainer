const { test, expect } = require("@playwright/test");
const itemId = "DEMO_1_36";
const itemTitle = "Document authentication options";

async function openFollowUp(page) {
    await page.goto("/");
    await page.getByRole("button", { name: "Work board", exact: true }).click();
    const card = page.locator(`[data-item-id="${itemId}"]`);
    await expect(card).toContainText("New code");
    await expect(card).toContainText("Author replied");
    await expect(card).toContainText("Checks changed");
    await card.getByRole("button", { name: itemTitle, exact: true }).click();
    return page.locator(".follow-up");
}

test("returning cards show changes beside the prior decision with paginated evidence", async ({
    page,
}) => {
    const section = await openFollowUp(page);
    await expect(
        section.getByRole("heading", { name: "Since your last review" }),
    ).toBeVisible();
    await expect(section.locator(".saved-decision")).toContainText("Done");
    await expect(section.locator(".saved-decision")).toContainText(
        "Reviewed the previous code",
    );
    await expect(page.getByLabel("Disposition", { exact: true })).toHaveValue(
        "inbox",
    );
    await expect(section.locator(".follow-up-entry")).toHaveCount(20);
    await expect(section.locator(".follow-up-entry").first()).toContainText(
        "Author replied",
    );
    await expect(section.locator(".follow-up-entry script")).toHaveCount(0);
    expect(await page.evaluate(() => window.activityExecuted)).toBeUndefined();
    await section.getByRole("button", { name: "Load more activity" }).click();
    await expect(section.locator(".follow-up-entry")).toHaveCount(27);
    await expect(
        section.getByRole("heading", { name: "Checks now failing" }),
    ).toBeVisible();
    await expect(
        section.getByRole("heading", { name: "New code pushed" }),
    ).toBeVisible();
    await expect(section.locator('a[href$="/checks"]')).toHaveCount(1);
    await expect(
        section.getByRole("button", { name: "Load more activity" }),
    ).toHaveCount(0);
});

test("activity retry preserves the previous context and unsaved triage edits", async ({
    page,
}) => {
    const section = await openFollowUp(page);
    await page.getByLabel("Next action / note").fill("Keep my new assessment");
    await page.route(`**/api/items/${itemId}/follow-up?*`, (route) =>
        route.fulfill({
            status: 503,
            json: { detail: "History temporarily unavailable" },
        }),
    );
    await section.getByRole("button", { name: "Load more activity" }).click();
    await expect(section.getByRole("alert")).toContainText(
        "History temporarily unavailable",
    );
    await expect(section.locator(".follow-up-entry")).toHaveCount(20);
    await expect(page.getByLabel("Next action / note")).toHaveValue(
        "Keep my new assessment",
    );
    await page.unroute(`**/api/items/${itemId}/follow-up?*`);
    await section.getByRole("button", { name: "Retry activity" }).click();
    await expect(section.locator(".follow-up-entry")).toHaveCount(27);
    await expect(page.getByLabel("Next action / note")).toHaveValue(
        "Keep my new assessment",
    );
});

test("missing history and later decision edits are explicit", async ({
    page,
}) => {
    await page.route(`**/api/items/${itemId}`, async (route) => {
        const data = await (await route.fetch()).json();
        data.item.follow_up.history_gap = true;
        data.item.follow_up.decision_at_review = false;
        data.follow_up_changes = { total: 0, entries: [] };
        await route.fulfill({ json: data });
    });
    const section = await openFollowUp(page);
    await expect(section).toContainText(
        "Some source revisions have no retained observations",
    );
    await expect(section).toContainText("details are no longer retained");
    await expect(section).toContainText(
        "This decision was edited after your last review",
    );
    await expect(
        section.getByRole("heading", { name: "Latest saved decision" }),
    ).toBeVisible();
    await expect(section).not.toContainText("No new source activity observed");
});
