import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:3100";
const apiURL = process.env.E2E_API_URL ?? "http://127.0.0.1:8100";
const databaseURL = process.env.E2E_DATABASE_URL;
const migrationDatabaseURL = process.env.E2E_MIGRATION_DATABASE_URL;
const externalServers = process.env.E2E_EXTERNAL_SERVERS === "true";

if (!databaseURL || !migrationDatabaseURL) {
  throw new Error(
    "E2E tests must be started through scripts/run_e2e_tests.py with an isolated database",
  );
}

const parsedDatabaseURL = new URL(databaseURL);
const databaseName = parsedDatabaseURL.pathname.replace(/^\//, "");
if (
  !["127.0.0.1", "localhost", "::1"].includes(parsedDatabaseURL.hostname) ||
  !(databaseName.endsWith("_test") || databaseName.endsWith("_pytest"))
) {
  throw new Error(
    "E2E_DATABASE_URL must reference a local *_test or *_pytest database",
  );
}

const nodeExecutable = JSON.stringify(process.execPath);
const pythonExecutable = JSON.stringify(process.env.PYTHON_BINARY ?? "python");
const nextExecutable = JSON.stringify(
  path.resolve("node_modules", "next", "dist", "bin", "next"),
);

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
  webServer: externalServers
    ? undefined
    : [
        {
          command:
            `${pythonExecutable} -m uvicorn teamora_api.main:app --app-dir services/api ` +
            "--host 127.0.0.1 --port 8100",
          url: `${apiURL}/api/v1/health/ready`,
          timeout: 120_000,
          reuseExistingServer: false,
          env: {
            ...process.env,
            APP_ENV: "test",
            ENABLE_CALL_SIMULATOR: "true",
            DATABASE_URL: databaseURL,
            MIGRATION_DATABASE_URL: migrationDatabaseURL,
            CORS_ORIGINS: `${baseURL},http://localhost:3000,http://localhost:8080`,
          },
        },
        {
          command: `${nodeExecutable} ${nextExecutable} dev apps/web -p 3100`,
          url: baseURL,
          timeout: 120_000,
          reuseExistingServer: false,
          env: { ...process.env, API_INTERNAL_URL: apiURL },
        },
      ],
});
