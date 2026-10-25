/**
 * FIR-41 第 6 项：手机布局回归。用真实 Chromium 打开实际 V4 页面，接口用 tests/v6/r67_make_docs.py
 * （真实行情 fixture + Python Core）生成的 doc 打桩，在 320 / 390 / 768 / 1280 宽度下检查：
 *   - 首页、个股分析（W底 002436、头肩 002594）、自选股、市场扫描：页面不出现横向滚动（文档宽 = 视口宽）
 *   - 页面脚本无报错；回测开关点得动
 * 另做源码检查：多列卡片网格（三列，或间距 ≥12px 的两列）必须带 m-stack 类，窄屏才会变单列。
 *
 * 运行：node tests/v6/test_mobile_layout.js（需要 playwright-core@1.56.1 及其 Chromium：
 *       npx playwright install chromium；或用 CHROME_PATH 指定其他 Chromium / Chrome）
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { execFileSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..', '..');
const PAGE = process.env.V4_PAGE || path.join(ROOT, 'v6', 'app-builder', 'client', 'public', 'v4', 'index.html');
const HTML = fs.readFileSync(PAGE, 'utf8');

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log('  ✓ ' + name); }
  catch (e) { failed++; console.log('  ✗ ' + name + '\n      ' + (e && e.stack ? e.stack.split('\n').slice(0, 6).join('\n      ') : e)); annotate(name, e); }
}

/** 默认用 Playwright 自带、与 playwright-core 版本匹配的 Chromium；CHROME_PATH 可指定其他浏览器 */
function launchOptions() {
  return process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {};
}

/** 在 GitHub Actions 里把失败写成注解，便于从 API 直接看到原因 */
function annotate(title, err) {
  if (!process.env.GITHUB_ACTIONS) return;
  const msg = String(err && err.stack ? err.stack.split('\n').slice(0, 6).join(' | ') : err).replace(/\r?\n/g, ' | ');
  console.log(`::error title=${title.replace(/[,:\n]/g, ' ')}::${msg}`);
}

(async () => {
  console.log('源码约束');
  await test('多列卡片网格都带 m-stack（三列，或间距 ≥12px 的两列）；窄屏样式存在', () => {
    const bad = [];
    const re = /<div([^>]*)style="([^"]*grid-template-columns:\s*([^;"]+)[^"]*)"/g;
    let m;
    while ((m = re.exec(HTML))) {
      const cols = m[3].trim();
      const gap = Number((m[2].match(/gap:\s*(\d+)px/) || [])[1] || 0);
      const isCardGrid = /^1fr 1fr 1fr$/.test(cols) || (/^1fr 1fr$/.test(cols) && gap >= 12);
      if (isCardGrid && !/class="[^"]*\bm-stack\b/.test(m[1])) {
        bad.push(`第 ${HTML.slice(0, m.index).split('\n').length} 行：${cols} gap ${gap}px 没有 m-stack`);
      }
    }
    assert.deepStrictEqual(bad, [], bad.join('\n'));
    assert.ok(/@media \(max-width: 640px\)[\s\S]*?\.m-stack \{ grid-template-columns: 1fr !important; \}/.test(HTML), '缺少窄屏单列样式');
    assert.ok(!/minmax\(\s*(280|300)px/.test(HTML), '固定最小宽度的自适应网格在窄屏会溢出，请用 minmax(min(Npx, 100%), 1fr)');
  });

  const DOCS = JSON.parse(execFileSync(process.env.PYTHON || 'python3', [path.join(ROOT, 'tests', 'v6', 'r67_make_docs.py')],
    { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }));
  const { chromium } = require('playwright-core');
  const server = http.createServer((req, res) => {
    const u = new URL(req.url, 'http://x');
    const send = (code, body, type = 'application/json') => { res.writeHead(code, { 'Content-Type': type }); res.end(typeof body === 'string' ? body : JSON.stringify(body)); };
    if (u.pathname.endsWith('/v4/index.html')) return send(200, HTML, 'text/html; charset=utf-8');
    const api = u.pathname.replace(/^.*\/api/, '');
    if (api === '/health') {
      return send(200, { status: 'ok', python_available: true, core_installed: true, core_analyzer: 'real_core',
        watchlist_store: { kind: 'postgres', ready: true, error: null } });
    }
    let m;
    if ((m = api.match(/^\/analyze\/(\d{6})/))) return send(200, m[1] === '002594' ? DOCS.hs_002594 : DOCS.base_002436);
    if (api.startsWith('/watchlist')) return send(200, { items: ['002436', '002594', '600519'], tombstones: [], count: 3, rev: 3, storage: 'postgres' });
    return send(404, { message: 'stub' });
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}/app/app_test/v4/index.html`;
  const browser = await chromium.launch(launchOptions());

  const width = (page) => page.evaluate(() => ({ vw: document.documentElement.clientWidth, sw: document.documentElement.scrollWidth }));
  const offenders = (page) => page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    return [...document.querySelectorAll('body *')].filter((el) => el.getBoundingClientRect().right > vw + 1)
      .slice(0, 5).map((el) => `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''} ${(el.innerText || '').trim().slice(0, 30)}`);
  });

  try {
    for (const w of [320, 390, 768, 1280]) {
      console.log(`宽度 ${w}px`);
      const ctx = await browser.newContext({ viewport: { width: w, height: 844 }, isMobile: w < 700, hasTouch: w < 700 });
      const page = await ctx.newPage();
      const errors = [];
      page.on('pageerror', (e) => errors.push(String(e)));
      const views = [
        ['首页', async () => { await page.goto(base); await page.waitForTimeout(500); }],
        ['个股分析 002436（W底）', async () => {
          await page.goto(base + '?code=002436');
          await page.waitForFunction(() => document.querySelector('#chart60, canvas') && /结果指纹|Python Core/.test(document.body.innerText), null, { timeout: 15000 });
          await page.waitForTimeout(800);
        }],
        ['个股分析 002594（头肩）', async () => {
          await page.goto(base + '?code=002594');
          await page.waitForFunction(() => document.querySelector('canvas') && /002594/.test(document.body.innerText), null, { timeout: 15000 });
          await page.waitForTimeout(800);
        }],
        ['自选股', async () => { await page.goto(base); await page.waitForTimeout(300); await page.evaluate(() => switchTab('watchlist')); await page.waitForTimeout(500); }],
        ['市场扫描', async () => { await page.evaluate(() => switchTab('scanner')); await page.waitForTimeout(300); }],
      ];
      for (const [label, go] of views) {
        await test(`${label}：无横向滚动`, async () => {
          await go();
          const { vw, sw } = await width(page);
          assert.ok(sw <= vw, `文档宽 ${sw} > 视口 ${vw}：${(await offenders(page)).join(' | ')}`);
        });
      }
      await test('回测开关点得动（隐藏的 checkbox 不占位但仍可切换）', async () => {
        await page.goto(base);
        await page.waitForTimeout(300);
        await page.evaluate(() => switchTab('analyze'));
        await page.click('#backtestSlider');
        assert.strictEqual(await page.$eval('#backtestMode', (el) => el.checked), true);
        assert.notStrictEqual(await page.$eval('#backtestDate', (el) => getComputedStyle(el).display), 'none', '打开回测后日期框应显示');
      });
      await test('页面脚本无报错', () => assert.deepStrictEqual(errors, []));
      await ctx.close();
    }
  } finally {
    await browser.close();
    server.close();
  }
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); annotate('mobile layout test crashed', e); process.exit(1); });
