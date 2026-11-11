import { defineConfig } from '@playwright/test';

const port = process.env.APP_PORT || '8082';
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  outputDir: 'artifacts/test-results',
  reporter: [
    ['list'],
    ['junit', { outputFile: 'artifacts/junit.xml' }],
    ['json', { outputFile: 'artifacts/playwright-results.json' }],
    ['html', { outputFolder: 'artifacts/playwright-report', open: 'never' }],
  ],
  use: {
    baseURL: process.env.BASE_URL || `http://127.0.0.1:${port}`,
    browserName: 'chromium',
    viewport: { width: 1280, height: 800 },
    locale: 'ru-RU',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    launchOptions: executablePath ? { executablePath } : {},
  },
});
