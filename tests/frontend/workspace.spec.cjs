const { test, expect } = require("@playwright/test");

const targetId = "DEMO_1_42";
const targetTitle = "Upgrade HTTP client and retry transient failures";
const targetPath = `/api/items/${targetId}/triage`;

async function resetDecision(request) {
    const response = await request.put(targetPath, {
        headers: { "X-Maintainer-Request": "1" },
        data: { disposition: "inbox", note: "" },
    });
    expect(response.ok()).toBeTruthy();
}

test.beforeEach(async ({ page, request }) => {
    await resetDecision(request);
    await page.goto("/");
    await expect(page.locator("#results")).toHaveAttribute(
        "aria-busy",
        "false",
    );
});

test("renders Markdown safely and offers the source", async ({ page }) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.getByRole("button", { name: targetTitle, exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(
        dialog.getByRole("heading", { name: "Reproduction" }),
    ).toBeVisible();
    await expect(dialog.locator(".description .markdown strong")).toHaveText(
        "important",
    );
    await expect(dialog.locator(".markdown table")).toBeVisible();
    await expect(dialog.locator(".markdown input[type=checkbox]")).toHaveCount(
        2,
    );
    await expect(dialog.locator(".markdown input").first()).toBeDisabled();
    await expect(dialog.locator(".markdown pre code")).toContainText(
        'print("retry")',
    );
    await expect(
        dialog.locator('.markdown a[href="https://github.com/example/orbit"]'),
    ).toHaveAttribute("rel", "noopener noreferrer");
    expect(await page.evaluate(() => window.markdownExecuted)).toBeUndefined();
    await expect(
        dialog.locator(
            '.markdown script, .markdown svg, .markdown iframe, .markdown [onerror], .markdown [onload], .markdown a[href^="javascript:"]',
        ),
    ).toHaveCount(0);
    await dialog.getByRole("button", { name: "Markdown source" }).click();
    await expect(dialog.locator(".markdown-source")).toContainText(
        "## Reproduction",
    );
    await dialog
        .getByRole("button", { name: "Description", exact: true })
        .click();
    await expect(
        dialog.getByRole("heading", { name: "Reproduction" }),
    ).toBeVisible();
    await dialog.getByRole("button", { name: "Close details" }).click();
    await expect(dialog).not.toBeVisible();
    expect(errors).toEqual([]);
});

test("persists a triage decision, filters lanes, and snoozes locally", async ({
    page,
    request,
}) => {
    await page
        .getByRole("button", { name: "Triage inbox", exact: false })
        .click();
    await page.getByRole("button", { name: targetTitle, exact: true }).click();
    await page
        .getByLabel("Disposition", { exact: true })
        .selectOption("planned");
    await page
        .getByLabel("Next action / note")
        .fill("Reproduce with the latest client");
    await page
        .getByRole("button", { name: "Save decision", exact: true })
        .click();
    await expect(
        page.getByRole("status").filter({ hasText: "Decision saved" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Close details" }).click();
    await expect(
        page.getByRole("button", { name: targetTitle, exact: true }),
    ).toHaveCount(0);
    await page.locator('[data-disposition="planned"]').click();
    await page.getByRole("button", { name: targetTitle, exact: true }).click();
    await expect(page.getByLabel("Next action / note")).toHaveValue(
        "Reproduce with the latest client",
    );
    await page
        .getByLabel("Disposition", { exact: true })
        .selectOption("snoozed");
    await page.getByLabel("Snooze until").fill("2099-01-01T09:00");
    await page
        .getByRole("button", { name: "Save decision", exact: true })
        .click();
    await expect(
        page.getByRole("status").filter({ hasText: "Decision saved" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Close details" }).click();
    await page.locator('[data-disposition="snoozed"]').click();
    await expect(
        page.getByRole("button", { name: targetTitle, exact: true }),
    ).toBeVisible();
    await page.reload();
    const item = (await (await request.get(`/api/items/${targetId}`)).json())
        .item;
    expect(item.disposition).toBe("snoozed");
    expect(item.note).toBe("Reproduce with the latest client");
    expect(item.state).toBe("open");
    await expect(
        page.getByRole("button", { name: targetTitle, exact: true }),
    ).toHaveCount(0);
});

test("supports filtering, sorting, keyboard search, and theme preference", async ({
    page,
    isMobile,
}) => {
    if (!isMobile) await page.keyboard.press("/");
    else await page.getByRole("searchbox").focus();
    await expect(page.getByRole("searchbox")).toBeFocused();
    await page.getByRole("searchbox").fill("HTTP");
    await expect(page.locator(".item-row")).toHaveCount(1);
    await page
        .getByRole("button", { name: "Clear filters", exact: true })
        .click();
    await page
        .getByRole("button", { name: "High priority", exact: true })
        .click();
    await expect(page.locator(".item-row")).toHaveCount(2);
    await page.getByLabel("Sort items").selectOption("updated");
    await expect(page.locator(".item-title").first()).toHaveText(
        "Rotate service account permissions",
    );
    await page
        .getByLabel("Repository", { exact: true })
        .selectOption("example/orbit");
    await expect(page.locator(".item-row")).toHaveCount(1);
    await page
        .getByRole("button", { name: "All indexed items", exact: true })
        .click();
    await expect(page.locator(".item-row")).toHaveCount(6);
    await page.getByLabel("Item state").selectOption("merged");
    await expect(page.locator(".item-row")).toHaveCount(1);
    await page
        .getByRole("button", { name: "Clear filters", exact: true })
        .click();
    await expect(page.locator(".item-row")).toHaveCount(6);
    await expect(page.getByLabel("Item state")).toHaveValue("");
    await page
        .getByRole("button", { name: "Repositories", exact: true })
        .first()
        .click();
    await expect(page.locator(".repo-card")).toHaveCount(3);
    expect(
        await page.evaluate(
            () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
    ).toBeTruthy();
    await page.getByRole("button", { name: "Switch to light theme" }).click();
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

test("retains edits after a failed save and can retry", async ({ page }) => {
    await page.getByRole("button", { name: targetTitle, exact: true }).click();
    await page.getByLabel("Next action / note").fill("Do not lose this note");
    await page.route(`**${targetPath}`, (route) =>
        route.fulfill({
            status: 503,
            contentType: "application/json",
            body: JSON.stringify({ detail: "Database unavailable" }),
        }),
    );
    await page
        .getByRole("button", { name: "Save decision", exact: true })
        .click();
    await expect(page.getByRole("alert")).toHaveText("Database unavailable");
    await expect(page.getByLabel("Next action / note")).toHaveValue(
        "Do not lose this note",
    );
    await page.unroute(`**${targetPath}`);
    await page
        .getByRole("button", { name: "Save decision", exact: true })
        .click();
    await expect(
        page.getByRole("status").filter({ hasText: "Decision saved" }),
    ).toBeVisible();
});

test("save and next advances through the inbox", async ({ page }) => {
    await page.getByRole("button", { name: "Start triaging" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.locator("#detail-title")).toHaveText(targetTitle);
    await page.getByLabel("Disposition", { exact: true }).selectOption("done");
    await page.getByRole("button", { name: "Save & next" }).click();
    await expect(page.locator("#detail-title")).toHaveText(
        "Rotate service account permissions",
    );
});

test("protects unsaved notes when moving between items", async ({
    page,
    request,
}) => {
    await page.getByRole("button", { name: targetTitle, exact: true }).click();
    await page.getByLabel("Next action / note").fill("Still being edited");
    page.once("dialog", (dialog) => dialog.dismiss());
    await page.getByRole("button", { name: "Next item", exact: true }).click();
    await expect(page.locator("#detail-title")).toHaveText(targetTitle);
    await expect(page.getByLabel("Next action / note")).toHaveValue(
        "Still being edited",
    );
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "Next item", exact: true }).click();
    await expect(page.locator("#detail-title")).toHaveText(
        "Rotate service account permissions",
    );
    const item = (await (await request.get(`/api/items/${targetId}`)).json())
        .item;
    expect(item.note).toBe("");
});

test("shows paginated PR comments, review state, and inline context safely", async ({
    page,
}) => {
    const path = `**/api/items/${targetId}/comments?*`;
    await page.route(path, async (route) => {
        const data = await (await route.fetch()).json();
        if (new URL(route.request().url()).searchParams.get("offset") === "0") {
            data.comments = data.comments.slice(0, 2);
            data.comments[0].body +=
                '<script>window.commentExecuted = true</script><img src="x" onerror="window.commentExecuted = true">';
        }
        await route.fulfill({ json: data });
    });
    await page.getByRole("button", { name: targetTitle, exact: true }).click();
    await expect(page.locator(".comment")).toHaveCount(2);
    await expect(page.locator(".comment-path")).toHaveText("src/retry.py:5");
    await page.getByText("Code context", { exact: true }).click();
    await expect(page.locator(".comment details pre code")).toContainText(
        "+retry()",
    );
    await page
        .getByRole("button", { name: "Load more comments", exact: true })
        .click();
    await expect(page.locator(".comment")).toHaveCount(3);
    await expect(
        page.locator(".comment-meta").filter({ hasText: "commented" }),
    ).toHaveCount(1);
    await expect(page.locator(".comment .markdown strong")).toHaveText(
        "review",
    );
    await expect(
        page.locator(".comment script, .comment [onerror]"),
    ).toHaveCount(0);
    expect(await page.evaluate(() => window.commentExecuted)).toBeUndefined();
    await page
        .getByLabel("Next action / note")
        .fill("Check **retries** and `timeouts`.");
    await expect(page.locator(".note-preview strong")).toHaveText("retries");
    await expect(page.locator(".note-preview code")).toHaveText("timeouts");
});

test("retries comments independently and displays repository stars and forks", async ({
    page,
}) => {
    const path = `**/api/items/${targetId}/comments?*`;
    await page.route(path, (route) =>
        route.fulfill({
            status: 503,
            json: { detail: "Comments unavailable" },
        }),
    );
    await page.getByRole("button", { name: targetTitle, exact: true }).click();
    await expect(
        page.getByRole("button", { name: "Retry comments" }),
    ).toBeVisible();
    await expect(
        page.getByRole("heading", { name: "Reproduction" }),
    ).toBeVisible();
    await page.unroute(path);
    await page.getByRole("button", { name: "Retry comments" }).click();
    await expect(page.locator(".comment")).toHaveCount(3);
    await page.getByRole("button", { name: "Close details" }).click();
    await page
        .getByRole("button", { name: "Repositories", exact: true })
        .first()
        .click();
    const orbit = page
        .locator(".repo-card")
        .filter({
            has: page.getByRole("link", { name: "example/orbit", exact: true }),
        });
    await expect(
        orbit.getByRole("link", { name: "1,284 stars", exact: true }),
    ).toHaveAttribute("href", "https://github.com/example/orbit/stargazers");
    await expect(
        orbit.getByRole("link", { name: "96 forks", exact: true }),
    ).toHaveAttribute("href", "https://github.com/example/orbit/forks");
    await expect(
        page.getByRole("link", { name: "0 stars", exact: true }),
    ).toBeVisible();
    await page.route("**/api/overview", async (route) => {
        const data = await (await route.fetch()).json();
        delete data.repos[0].raw.stargazers_count;
        delete data.repos[0].raw.forks_count;
        await route.fulfill({ json: data });
    });
    await page.locator("#refresh").click();
    await expect(
        page.getByRole("link", { name: "— stars", exact: true }),
    ).toBeVisible();
    await expect(
        page.getByRole("link", { name: "— forks", exact: true }),
    ).toBeVisible();
    expect(
        await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
        ),
    ).toBeTruthy();
});
