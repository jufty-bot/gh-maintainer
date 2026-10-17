const { defineConfig, devices } = require("@playwright/test");

module.exports = defineConfig({
    testDir: "./tests/frontend",
    fullyParallel: false,
    workers: 1,
    use: { baseURL: "http://127.0.0.1:8876", trace: "retain-on-failure" },
    projects: [
        { name: "desktop", use: { ...devices["Desktop Chrome"] } },
        { name: "mobile", use: { ...devices["Pixel 7"] } },
    ],
    webServer: {
        command: "uv run --locked python tests/frontend/serve.py",
        url: "http://127.0.0.1:8876/api/overview",
        reuseExistingServer: false,
        timeout: 30000,
    },
});
