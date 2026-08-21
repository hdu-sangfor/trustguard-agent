import { defineConfig, devices } from "@playwright/test";

const webServerPort = Number(process.env.PLAYWRIGHT_PORT ?? 4173);
const baseURL = `http://127.0.0.1:${webServerPort}`;

export default defineConfig({
  testDir: "./tests",
  outputDir: "./node_modules/.cache/playwright-results",
  fullyParallel: true,
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `npm run build && npm run preview -- --host 127.0.0.1 --port ${webServerPort}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], channel: "chromium" } },
  ],
});
