import { execFileSync } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect } from '@playwright/test';

const projectRoot = fileURLToPath(new URL('../', import.meta.url));
const artifactDir = path.join(projectRoot, 'artifacts');

// The expected values come directly from PostgreSQL, independently of the API.
function readDatabaseItems() {
  const sql = `SELECT COALESCE(json_agg(t ORDER BY t.id), '[]'::json)::text
    FROM (SELECT id, name FROM items ORDER BY id) AS t;`;
  const command = 'exec psql -X -qAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"';
  const output = execFileSync('docker', [
    'compose', 'exec', '-T', 'db', 'sh', '-c', command, 'sh', sql,
  ], {
    cwd: projectRoot,
    encoding: 'utf8',
    timeout: 15_000,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const items = JSON.parse(output.trim());
  expect(Array.isArray(items)).toBe(true);
  for (const item of items) {
    expect(Number.isInteger(item.id)).toBe(true);
    expect(typeof item.name).toBe('string');
  }
  return items;
}

function expectedCells(items) {
  return items.map(({ id, name }) => [String(id), name]);
}

async function readTable(page) {
  return page.locator('#items tr').evaluateAll(rows => rows.map(row =>
    Array.from(row.querySelectorAll('td'), cell => cell.textContent),
  ));
}

async function waitUntilLoaded(page, count) {
  await expect(page.locator('#status')).toHaveText(
    `Получено записей: ${count}. Подключение к базе работает.`,
  );
  await expect(page.locator('#refresh')).toBeEnabled();
}

test('health endpoint confirms application and database connectivity', async ({ request }) => {
  const response = await request.get('/healthz');
  expect(response.status()).toBe(200);
  expect((await response.text()).trim()).toBe('ok');
});

test('every database record matches the API and the rendered table', async ({ page, request }, testInfo) => {
  const databaseItems = readDatabaseItems();
  const response = await request.get('/api/items');
  expect(response.status()).toBe(200);
  expect(response.headers()['content-type']).toContain('application/json');
  const apiItems = await response.json();
  expect(apiItems).toEqual(databaseItems);

  await page.goto('/');
  await waitUntilLoaded(page, databaseItems.length);
  await expect(page.locator('table')).toBeVisible();
  await expect.poll(() => readTable(page)).toEqual(expectedCells(databaseItems));
  for (const row of await page.locator('#items tr').all()) {
    await expect(row).toBeVisible();
  }

  await mkdir(artifactDir, { recursive: true });
  const evidence = {
    checkedAt: new Date().toISOString(),
    database: databaseItems,
    api: apiItems,
    browserTable: await readTable(page),
    equal: true,
  };
  const evidencePath = path.join(artifactDir, 'data-comparison.json');
  await writeFile(evidencePath, JSON.stringify(evidence, null, 2) + '\n');
  await testInfo.attach('SQL, API and browser comparison', {
    path: evidencePath,
    contentType: 'application/json',
  });
  const screenshotPath = path.join(artifactDir, 'frontend.png');
  await page.screenshot({ path: screenshotPath, fullPage: true });
  await testInfo.attach('Application in a real browser', {
    path: screenshotPath,
    contentType: 'image/png',
  });
});

test('refresh makes a new API request and displays the current database values', async ({ page }) => {
  const initialItems = readDatabaseItems();
  await page.goto('/');
  await waitUntilLoaded(page, initialItems.length);

  const nextResponse = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/items'
    && response.request().method() === 'GET',
  );
  await page.locator('#refresh').click();
  const response = await nextResponse;
  expect(response.status()).toBe(200);
  const apiItems = await response.json();
  const databaseItems = readDatabaseItems();
  expect(apiItems).toEqual(databaseItems);
  await waitUntilLoaded(page, databaseItems.length);
  await expect.poll(() => readTable(page)).toEqual(expectedCells(databaseItems));
});
