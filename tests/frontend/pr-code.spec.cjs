const { test, expect } = require("@playwright/test");

test("PR files and commits are safe, readable, and navigable", async ({
    page,
}) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/");
    await page
        .getByRole("button", {
            name: "Upgrade HTTP client and retry transient failures",
            exact: true,
        })
        .click();
    const dialog = page.getByRole("dialog");
    await dialog
        .getByRole("button", { name: "Files Changed", exact: true })
        .click();
    await expect(dialog.locator(".git-diff .d2h-file-wrapper")).toHaveCount(1);
    await expect(
        dialog.getByRole("button", {
            name: page.viewportSize().width <= 700 ? "Unified" : "Side by side",
            exact: true,
        }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(dialog.locator(".d2h-code-line-ctn").last()).toContainText(
        "timeout = 30",
    );
    await dialog
        .getByRole("button", { name: "Side by side", exact: true })
        .click();
    const sides = dialog.locator(".d2h-file-side-diff");
    await expect(sides).toHaveCount(2);
    await expect(sides.nth(0)).toContainText("timeout = 10");
    await expect(sides.nth(1)).toContainText("timeout = 30");
    await expect(
        sides.nth(0).locator(".d2h-code-side-linenumber").last(),
    ).toHaveText("1");
    await dialog.getByRole("button", { name: "Unified", exact: true }).click();
    await expect(dialog.locator(".d2h-file-side-diff")).toHaveCount(0);
    await expect(dialog.locator(".git-diff")).toContainText("timeout = 10");
    await dialog.getByRole("button", { name: "Raw diff", exact: true }).click();
    await expect(dialog.locator(".git-diff-raw")).toContainText("diff --git");
    await expect(dialog.getByText(/Head demo-head/)).toBeVisible();
    await dialog.getByRole("button", { name: "Commits", exact: true }).click();
    const link = dialog.locator(".pr-commits a");
    await expect(link).toHaveAttribute(
        "href",
        "https://github.com/example/orbit/commit/demo-head-1-42",
    );
    await expect(link).toHaveAttribute("rel", "noopener noreferrer");
    await dialog.getByText("Commit message", { exact: true }).click();
    await expect(dialog.locator(".pr-commits pre")).toContainText(
        "Synthetic demo commit.",
    );
    await dialog
        .getByRole("button", { name: "Conversation", exact: true })
        .click();
    await expect(
        dialog.getByRole("heading", { name: "Reproduction" }),
    ).toBeVisible();
    expect(errors).toEqual([]);
});

test("renders multiple hunks, new files, renames and binary notices with safe navigation", async ({
    page,
}) => {
    const patch = [
        "diff --git a/src/app.js b/src/app.js",
        "--- a/src/app.js",
        "+++ b/src/app.js",
        "@@ -2,3 +2,4 @@",
        " keep " + "x".repeat(160),
        "-old value",
        "+new value",
        "+added value",
        " context",
        "@@ -10 +11 @@",
        "-old tail",
        "+new tail",
        "diff --git a/new.txt b/new.txt",
        "new file mode 100644",
        "--- /dev/null",
        "+++ b/new.txt",
        "@@ -0,0 +1 @@",
        "+created",
        "diff --git a/old.txt b/old.txt",
        "deleted file mode 100644",
        "--- a/old.txt",
        "+++ /dev/null",
        "@@ -1 +0,0 @@",
        "-removed",
        "diff --git a/old-name.txt b/new-name.txt",
        "similarity index 100%",
        "rename from old-name.txt",
        "rename to new-name.txt",
        "diff --git a/image.png b/image.png",
        "Binary files a/image.png and b/image.png differ",
        "",
    ].join("\n");
    await page.route("**/api/items/DEMO_1_42/files", (route) =>
        route.fulfill({
            json: {
                head_sha: "latest",
                base_sha: "base",
                fetched_at: "2026-10-20T00:00:00Z",
                stale: false,
                coverage_error: null,
                diff: patch,
            },
        }),
    );
    await page.goto("/");
    await page
        .getByRole("button", {
            name: "Upgrade HTTP client and retry transient failures",
            exact: true,
        })
        .click();
    const dialog = page.getByRole("dialog");
    await dialog
        .getByRole("button", { name: "Files Changed", exact: true })
        .click();
    await dialog
        .getByRole("button", { name: "Side by side", exact: true })
        .click();
    const files = dialog.locator(".d2h-file-wrapper");
    await expect(files).toHaveCount(5);
    const sides = files.first().locator(".d2h-file-side-diff");
    await expect(sides.nth(0)).toContainText("old tail");
    await expect(sides.nth(1)).toContainText("new tail");
    await expect(
        sides.nth(0).locator(".d2h-code-side-linenumber").last(),
    ).toHaveText("10");
    await expect(
        sides.nth(1).locator(".d2h-code-side-linenumber").last(),
    ).toHaveText("11");
    await sides.nth(0).evaluate((node) => {
        node.scrollLeft = 80;
        node.dispatchEvent(new Event("scroll"));
    });
    await expect
        .poll(() => sides.nth(1).evaluate((node) => node.scrollLeft))
        .toBe(80);
    await expect(files.nth(1)).toContainText("created");
    await expect(files.nth(2)).toContainText("removed");
    await expect(files.nth(3)).toContainText("new-name.txt");
    await expect(files.nth(4)).toContainText("Binary file");
    await dialog.getByRole("link", { name: "image.png", exact: true }).click();
    await expect(files.nth(4)).toBeInViewport();
    expect(await page.evaluate(() => location.hash)).toBe("");
    const lightBackground = await sides
        .nth(1)
        .locator("td.d2h-ins")
        .last()
        .evaluate((node) => getComputedStyle(node).backgroundColor);
    await page.emulateMedia({ colorScheme: "dark" });
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    const darkBackground = await sides
        .nth(1)
        .locator("td.d2h-ins")
        .last()
        .evaluate((node) => getComputedStyle(node).backgroundColor);
    expect(darkBackground).not.toBe(lightBackground);
    await page.emulateMedia({ colorScheme: "light" });
    await dialog.getByRole("button", { name: "Raw diff", exact: true }).click();
    await expect(dialog.locator(".git-diff-raw")).toHaveText(patch);
});

test("diff failures can retry, and source text cannot execute HTML", async ({
    page,
}) => {
    let fail = true;
    await page.route("**/api/items/DEMO_1_42/files", (route) => {
        if (fail) return route.fulfill({ status: 503, body: "Unavailable" });
        return route.fulfill({
            json: {
                head_sha: "latest",
                base_sha: "base",
                fetched_at: "2026-10-20T00:00:00Z",
                stale: false,
                coverage_error: null,
                diff: 'diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -0,0 +1 @@\n+<img src=x onerror="window.diffExecuted=true">\n',
            },
        });
    });
    await page.goto("/");
    await page
        .getByRole("button", {
            name: "Upgrade HTTP client and retry transient failures",
            exact: true,
        })
        .click();
    const dialog = page.getByRole("dialog");
    await dialog
        .getByRole("button", { name: "Files Changed", exact: true })
        .click();
    await expect(
        dialog.getByRole("button", { name: "Retry", exact: true }),
    ).toBeVisible();
    fail = false;
    await dialog.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(dialog.locator(".git-diff")).toContainText("<img");
    await expect(dialog.locator(".git-diff img")).toHaveCount(0);
    expect(await page.evaluate(() => window.diffExecuted)).toBeUndefined();
});

test("issues have no PR code tabs", async ({ page }) => {
    await page.goto("/");
    const data = await (await page.request.get("/api/items?kind=issue")).json();
    const item = data.items[0];
    await page.getByRole("button", { name: item.title, exact: true }).click();
    await expect(
        page
            .getByRole("dialog")
            .getByRole("button", { name: "Files Changed", exact: true }),
    ).toHaveCount(0);
});
