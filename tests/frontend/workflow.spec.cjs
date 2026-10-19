const { test, expect } = require("@playwright/test");

const itemId = "DEMO_1_42";
const itemTitle = "Upgrade HTTP client and retry transient failures";
const headers = { "X-Maintainer-Request": "1" };

test.beforeEach(async ({ request }) => {
    await request.put(`/api/items/${itemId}/triage`, {
        headers,
        data: { disposition: "inbox", note: "" },
    });
});

test("guides first setup, authentication, sync progress, and partial retry", async ({
    page,
}) => {
    const options = {
        repo: null,
        owner: null,
        full: false,
        concurrency: 4,
        requests_per_second: 2,
    };
    let credential = false;
    let status = {
        state: "idle",
        message: "Ready to sync.",
        stages: 0,
        result: null,
        options,
    };
    let starts = 0;
    await page.route("**/api/overview", async (route) => {
        const data = await (await route.fetch()).json();
        data.counts.repositories = 0;
        data.repos = [];
        data.latest_run = null;
        await route.fulfill({ json: data });
    });
    await page.route("**/api/setup", (route) =>
        route.fulfill({
            json: {
                credential_available: credential,
                message: credential
                    ? "Server credential available. Access is verified during sync."
                    : "On the server, run gh auth login or set GH_TOKEN / GITHUB_TOKEN, then check again.",
                sync: status,
            },
        }),
    );
    await page.route("**/api/sync", async (route) => {
        if (route.request().method() === "POST") {
            starts++;
            const body = route.request().postDataJSON();
            expect(body.repo).toEqual(["example/orbit", "another/project"]);
            expect(body.owner).toBeNull();
            expect(route.request().headers()["x-maintainer-request"]).toBe("1");
            status = {
                ...status,
                state: "running",
                message: "Discovering repositories",
                stages: 1,
                options: body,
            };
            await route.fulfill({ status: 202, json: status });
        } else {
            status = {
                ...status,
                state: starts === 1 ? "partial" : "complete",
                message:
                    starts === 1
                        ? "Some repositories could not be synced. Check errors and retry."
                        : "Synced 2 of 2 repositories.",
                stages: 8,
                result: {
                    errors:
                        starts === 1 ? ["another/project: access denied"] : [],
                },
            };
            await route.fulfill({ json: status });
        }
    });
    await page.goto("/");
    await page.getByRole("button", { name: "Set up repositories" }).click();
    const setup = page.locator("#sync-dialog");
    await expect(
        setup.getByRole("button", { name: "Start sync" }),
    ).toBeDisabled();
    await expect(setup.locator("#credential-status")).toContainText(
        "gh auth login",
    );
    credential = true;
    await setup
        .getByRole("button", { name: "Check authentication again" })
        .click();
    await expect(
        setup.getByRole("button", { name: "Start sync" }),
    ).toBeEnabled();
    await setup.getByLabel("Repositories to sync").selectOption("repos");
    await setup
        .getByLabel("Repository names")
        .fill("example/orbit\nanother/project");
    await setup.getByRole("button", { name: "Start sync" }).click();
    await expect(setup.locator("#sync-progress")).toContainText(
        "Discovering repositories",
    );
    await expect(setup.locator("#sync-errors")).toContainText("access denied");
    await expect(setup.getByLabel("Repository names")).toHaveValue(
        "example/orbit\nanother/project",
    );
    await setup.getByRole("button", { name: "Retry sync" }).click();
    await expect(setup.locator("#sync-progress")).toContainText(
        "Synced 2 of 2",
    );
    await expect(setup.locator("#sync-errors li")).toHaveCount(0);
    expect(starts).toBe(2);
});

test("finishes review only after all comment pages and saves the inspected revision", async ({
    page,
    request,
}) => {
    await page.route(`**/api/items/${itemId}/comments?*`, async (route) => {
        const data = await (await route.fetch()).json();
        if (new URL(route.request().url()).searchParams.get("offset") === "0")
            data.comments = data.comments.slice(0, 2);
        await route.fulfill({ json: data });
    });
    await page.goto("/");
    await page.getByRole("button", { name: "Start triaging" }).click();
    const finish = page.getByRole("button", { name: "Finish review & next" });
    await expect(finish).toBeDisabled();
    await page.getByRole("button", { name: "Load more comments" }).click();
    await expect(finish).toBeEnabled();
    const original = (await (await request.get(`/api/items/${itemId}`)).json())
        .item;
    await page.getByLabel("Disposition", { exact: true }).selectOption("done");
    await page
        .getByLabel("Next action / note")
        .fill("Reviewed all comments and checks");
    await finish.click();
    await expect(page.locator("#detail-title")).not.toHaveText(itemTitle);
    const saved = (await (await request.get(`/api/items/${itemId}`)).json())
        .item;
    expect(saved.processing_state).toBe("processed");
    expect(saved.processed_revision).toBe(original.revision);
    expect(saved.processed_by).toBe("dashboard");
    expect(saved.disposition).toBe("done");
    expect(saved.note).toBe("Reviewed all comments and checks");
    expect(saved.state).toBe("open");
});

test("keeps edits after a stale review and reloads source explicitly", async ({
    page,
}) => {
    await page.goto("/");
    await page.getByRole("button", { name: itemTitle, exact: true }).click();
    await page.getByLabel("Next action / note").fill("Preserve this decision");
    await page
        .getByLabel("Disposition", { exact: true })
        .selectOption("waiting");
    await page.route(`**/api/items/${itemId}/triage`, (route) => {
        const body = route.request().postDataJSON();
        expect(body.revision).toBeGreaterThan(0);
        expect(body.finish_review).toBe(true);
        return route.fulfill({
            status: 409,
            json: {
                detail: "Item changed; read it again before saving triage.",
            },
        });
    });
    await page.getByRole("button", { name: "Finish review & next" }).click();
    await expect(page.getByRole("alert")).toContainText("Item changed");
    await expect(page.getByLabel("Next action / note")).toHaveValue(
        "Preserve this decision",
    );
    await page
        .getByRole("button", { name: "Reload source, keep edits" })
        .click();
    await expect(page.getByLabel("Next action / note")).toHaveValue(
        "Preserve this decision",
    );
    await expect(page.getByLabel("Disposition", { exact: true })).toHaveValue(
        "waiting",
    );
    await expect(
        page.getByRole("button", { name: "Finish review & next" }),
    ).toBeEnabled();
});

test("finishes the last matching item without needing another candidate", async ({
    page,
    request,
}) => {
    await page.goto("/");
    await page.getByRole("searchbox").fill("HTTP");
    await expect(page.locator(".item-row")).toHaveCount(1);
    await page.getByRole("button", { name: itemTitle, exact: true }).click();
    await page.getByRole("button", { name: "Finish review & next" }).click();
    await expect(page.locator("#detail")).not.toBeVisible();
    const saved = (await (await request.get(`/api/items/${itemId}`)).json())
        .item;
    expect(saved.processing_state).toBe("processed");
});

test("finish review advances across a list page boundary", async ({
    page,
    request,
}) => {
    const original = (await (await request.get(`/api/items/${itemId}`)).json())
        .item;
    const next = (await (await request.get("/api/items/DEMO_1_39")).json())
        .item;
    const rows = Array.from({ length: 24 }, (_, index) => ({
        ...original,
        id: `OTHER_${index}`,
        number: index + 100,
        title: `Other candidate ${index}`,
    }));
    rows.push(original);
    await page.route("**/api/items?*", (route) => {
        const query = new URL(route.request().url()).searchParams;
        if (query.get("q") !== "long-review") return route.continue();
        return route.fulfill({
            json: {
                total: 26,
                items: Number(query.get("offset")) === 25 ? [next] : rows,
            },
        });
    });
    await page.goto("/");
    await page.getByRole("searchbox").fill("long-review");
    await expect(page.locator(".item-row")).toHaveCount(25);
    await page.getByRole("button", { name: itemTitle, exact: true }).click();
    await page.getByRole("button", { name: "Finish review & next" }).click();
    await expect(page.locator("#detail-title")).toHaveText(next.title);
});

test("Kanban moves cards, preserves decisions, filters, and opens snooze setup", async ({
    page,
    request,
    isMobile,
}) => {
    await request.put(`/api/items/${itemId}/triage`, {
        headers,
        data: {
            disposition: "inbox",
            note: "Keep this next action",
            priority: "high",
            category: "bug_fix",
        },
    });
    await page.goto("/");
    await page.getByRole("button", { name: "Work board", exact: true }).click();
    await expect(page.locator(".kanban-column")).toHaveCount(5);
    await expect(page.getByLabel("Item state")).toHaveValue("");
    let card = page.locator(`[data-item-id="${itemId}"]`);
    await expect(page.locator('[data-lane="inbox"]')).toContainText(itemTitle);
    await card.getByRole("combobox").selectOption("planned");
    await expect(page.locator('[data-lane="planned"]')).toContainText(
        itemTitle,
    );
    if (!isMobile) {
        await card.dragTo(page.locator('[data-lane="waiting"]'));
        await expect(page.locator('[data-lane="waiting"]')).toContainText(
            itemTitle,
        );
    }
    const saved = (await (await request.get(`/api/items/${itemId}`)).json())
        .item;
    expect(saved.note).toBe("Keep this next action");
    expect(saved.priority).toBe("high");
    expect(saved.category).toBe("bug_fix");
    await page.getByRole("searchbox").fill("HTTP");
    await expect(page.locator(".kanban-card")).toHaveCount(1);
    await card.getByRole("combobox").selectOption("snoozed");
    await expect(page.getByLabel("Snooze until")).toBeVisible();
    await page.getByLabel("Snooze until").fill("2099-01-01T09:00");
    await page
        .getByRole("button", { name: "Save decision", exact: true })
        .click();
    await page.getByRole("button", { name: "Close details" }).click();
    await expect(page.locator('[data-lane="snoozed"]')).toContainText(
        itemTitle,
    );
    expect(
        await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
        ),
    ).toBeTruthy();
    await page.reload();
    await page.getByRole("button", { name: "Work board", exact: true }).click();
    await expect(page.locator('[data-lane="snoozed"]')).toContainText(
        itemTitle,
    );
});

test("Kanban paginates each lane and retains the card after a failed move", async ({
    page,
}) => {
    let template;
    await page.route("**/api/items?*", async (route) => {
        const query = new URL(route.request().url()).searchParams;
        const data = await (await route.fetch()).json();
        if (
            query.get("disposition") === "inbox" &&
            query.get("limit") === "10"
        ) {
            template ||= data.items.find((item) => item.id === itemId);
            if (query.get("offset") === "10")
                data.items = [
                    {
                        ...template,
                        id: "PAGE_2",
                        number: 900,
                        title: "Second page card",
                    },
                ];
            data.total = 11;
        }
        await route.fulfill({ json: data });
    });
    await page.route(`**/api/items/${itemId}/triage`, (route) =>
        route.fulfill({
            status: 409,
            json: { detail: "Item changed; reload before moving." },
        }),
    );
    await page.goto("/");
    await page.getByRole("button", { name: "Work board", exact: true }).click();
    const inbox = page.locator('[data-lane="inbox"]');
    await page
        .locator(`[data-item-id="${itemId}"]`)
        .getByRole("combobox")
        .selectOption("done");
    await expect(inbox.getByRole("alert")).toContainText("Item changed");
    await expect(
        page.locator(`[data-item-id="${itemId}"]`).getByRole("combobox"),
    ).toHaveValue("inbox");
    await page.getByRole("button", { name: "Next Inbox cards" }).click();
    await expect(inbox).toContainText("Second page card");
    await page.getByRole("button", { name: "Previous Inbox cards" }).click();
    await expect(inbox).toContainText(itemTitle);
});
