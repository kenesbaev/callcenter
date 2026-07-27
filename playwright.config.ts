import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:3100";
const apiURL = process.env.E2E_API_URL ?? "http://127.0.0.1:8100";

export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: ".test-artifacts/playwright-results",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [
    ["list"],
    [
      "html",
      { outputFolder: ".test-artifacts/playwright-report", open: "never" },
    ],
  ],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        ...(process.env.CI
          ? {}
          : { channel: process.env.PLAYWRIGHT_CHANNEL ?? "msedge" }),
      },
    },
  ],
  webServer: [
    {
      command:
        "py -3.12 -m uvicorn teamora_api.main:app --app-dir services/api --host 127.0.0.1 --port 8100",
      url: `${apiURL}/api/v1/health/ready`,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: {
        ...process.env,
        APP_ENV: "development",
        ENABLE_CALL_SIMULATOR: "true",
        CORS_ORIGINS: `${baseURL},http://localhost:3000,http://localhost:8080`,
      },
    },
    {
      command: "npm run dev --prefix apps/web -- -p 3100",
      url: baseURL,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: { ...process.env, API_INTERNAL_URL: apiURL },
    },
  ],
});
