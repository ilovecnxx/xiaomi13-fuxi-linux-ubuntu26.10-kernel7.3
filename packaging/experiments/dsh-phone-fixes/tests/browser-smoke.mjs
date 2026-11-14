import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile, mkdir } from 'node:fs/promises';
import path from 'node:path';

const harness = process.env.DSH_SOURCE || '/home/fuxi/Projects/deepseek-harness';
const require = createRequire(path.join(harness, 'apps/web/package.json'));
const { chromium } = require('playwright');
const state = process.env.DSH_WEB_STATE || path.join(process.env.HOME, '.local/state/dsh-web');
const artifacts = path.join(state, 'audit-screenshots');
await mkdir(artifacts, { recursive: true, mode: 0o700 });
const url = (await readFile(path.join(state, 'url.txt'), 'utf8')).trim();
const browser = await chromium.launch({
  executablePath: '/opt/chromium/root/usr/lib/chromium/chromium',
  headless: true,
  env: { ...process.env, LD_LIBRARY_PATH: '/opt/chromium/privatelib:/opt/chromium/root/usr/lib/chromium' },
  args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-features=Vulkan'],
});
try {
  const context = await browser.newContext({ viewport: { width: 432, height: 860 }, colorScheme: 'light' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => { errors.push(error.message); });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  console.log('Authenticated page loaded');
  await page.screenshot({ path: path.join(artifacts, 'boot.png') });
  await page.locator('button:visible').first().waitFor({ timeout: 45000 });
  await page.waitForTimeout(1500);
  const describe = () => page.evaluate(() => ({
    title: document.title,
    buttons: [...document.querySelectorAll('button')].filter(el => el.getBoundingClientRect().width > 0).map(el => el.getAttribute('aria-label') || el.title || el.textContent?.trim()).slice(0, 25),
    inputs: [...document.querySelectorAll('textarea,input,[contenteditable="true"]')].map(el => ({ tag: el.tagName, role: el.getAttribute('role'), placeholder: el.getAttribute('placeholder'), editable: el.getAttribute('contenteditable') })),
    dark: document.body.hasAttribute('data-ds-dark-theme'),
    overflow: document.documentElement.scrollWidth > window.innerWidth,
    textLength: document.body.innerText.length,
  }));
  const light = await describe();
  console.log(JSON.stringify({ mobileLight: light }));
  assert(light.textLength > 20, 'application is blank');
  assert.equal(light.overflow, false, 'horizontal overflow on mobile');
  await page.screenshot({ path: path.join(artifacts, 'mobile-light.png') });
  const editor = page.locator('[contenteditable="true"][role="textbox"]:visible');
  await editor.fill('DSH \u4e2d\u6587\u8f93\u5165\u6d4b\u8bd5');
  assert.equal(await editor.innerText(), 'DSH \u4e2d\u6587\u8f93\u5165\u6d4b\u8bd5');
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.waitForFunction(() => document.body.hasAttribute('data-ds-dark-theme'));
  assert.equal(await editor.innerText(), 'DSH \u4e2d\u6587\u8f93\u5165\u6d4b\u8bd5', 'theme change lost the draft');
  await editor.fill('');
  await page.getByRole('button', { name: '\u6253\u5f00\u4fa7\u8fb9\u680f', exact: true }).first().click();
  await page.getByRole('button', { name: '\u641c\u7d22\u4f1a\u8bdd', exact: true }).first().waitFor({ state: 'visible' });
  await page.keyboard.press('Escape');
  await page.screenshot({ path: path.join(artifacts, 'mobile-dark.png') });
  const dark = await describe();
  assert.equal(dark.overflow, false, 'horizontal overflow in dark mode');
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.screenshot({ path: path.join(artifacts, 'desktop-dark.png') });
  const desktop = await describe();
  assert.equal(desktop.overflow, false, 'horizontal overflow on desktop');
  assert.deepEqual(errors, [], 'uncaught browser errors');
  console.log(JSON.stringify({ mobileDark: dark.dark, desktop: { overflow: desktop.overflow, textLength: desktop.textLength }, pageErrors: errors.length }));
  await context.close();
} finally {
  await browser.close();
}
