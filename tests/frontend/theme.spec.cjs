const { test, expect } = require("@playwright/test");

test.use({ colorScheme: "dark" });

test("follows the system until a theme is selected and remembers that choice", async ({
    page,
}) => {
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.emulateMedia({ colorScheme: "light" });
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await page.getByRole("button", { name: "Switch to dark theme" }).click();
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.emulateMedia({ colorScheme: "dark" });
    await page.emulateMedia({ colorScheme: "light" });
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.getByRole("button", { name: "Switch to light theme" }).click();
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

test("allows switching themes when browser storage is unavailable", async ({
    page,
}) => {
    await page.addInitScript(() => {
        Object.defineProperty(window, "localStorage", {
            get() {
                throw new Error("Storage disabled");
            },
        });
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.getByRole("button", { name: "Switch to light theme" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    expect(errors).toEqual([]);
});
