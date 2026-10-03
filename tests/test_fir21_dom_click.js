/**
 * FIR-21 DOM/点击级集成测试
 * 验证真实点击→模态框→确认/取消的完整链路
 *
 * 运行方式: node tests/test_fir21_dom_click.js
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const HTML_PATH = path.join(__dirname, '..', 'kline_analyzer.html');

// ========== 测试框架 ==========
let passed = 0;
let failed = 0;
const failures = [];

function assert(condition, message) {
  if (condition) { passed++; console.log(`  ✅ ${message}`); }
  else { failed++; failures.push(message); console.error(`  ❌ ${message}`); }
}

function assertEqual(actual, expected, message) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) { passed++; console.log(`  ✅ ${message}`); }
  else { failed++; failures.push(`${message}\n     期望: ${e}\n     实际: ${a}`); console.error(`  ❌ ${message}\n     期望: ${e}\n     实际: ${a}`); }
}

// ========== DOM Mock ==========
// 简单的DOM元素mock，支持innerHTML、click、querySelector等
class MockElement {
  constructor(tagName = 'div') {
    this.tagName = tagName;
    this._innerHTML = '';
    this.children = [];
    this.style = {};
    this.attributes = {};
    this._eventListeners = {};
    this.parentNode = null;
    this.value = '';
    this.textContent = '';
    this.classList = {
      toggle: () => {},
      add: () => {},
      remove: () => {},
      contains: () => false,
    };
  }

  set innerHTML(html) {
    this._innerHTML = html;
    // 解析HTML，创建子元素（简化版，只解析button）
    this.children = [];
    const buttonRegex = /<button\s+onclick="([^"]+)"[^>]*>([^<]*)<\/button>/g;
    let match;
    while ((match = buttonRegex.exec(html)) !== null) {
      const btn = new MockElement('button');
      btn._onclick = match[1];
      btn.textContent = match[2];
      btn.parentNode = this;
      this.children.push(btn);
    }
  }

  get innerHTML() {
    return this._innerHTML;
  }

  click() {
    // 执行onclick属性（showConfirm使用btn.onclick = ...方式绑定）
    if (this.onclick) {
      this.onclick({ target: this, stopPropagation: () => {} });
    }
    // 执行_onclick（innerHTML解析出的onclick属性）
    if (this._onclick) {
      vm.runInContext(this._onclick, sandbox);
    }
    // 触发事件监听器
    if (this._eventListeners['click']) {
      this._eventListeners['click'].forEach(fn => fn({ target: this, stopPropagation: () => {} }));
    }
  }

  addEventListener(type, fn) {
    if (!this._eventListeners[type]) this._eventListeners[type] = [];
    this._eventListeners[type].push(fn);
  }

  querySelector(selector) {
    // 递归查找
    if (selector.startsWith('button')) {
      return this._findButton() || null;
    }
    return null;
  }

  querySelectorAll(selector) {
    if (selector.startsWith('button')) {
      return this._findAllButtons();
    }
    return [];
  }

  _findButton() {
    for (const child of this.children) {
      if (child.tagName === 'button') return child;
      const found = child._findButton ? child._findButton() : null;
      if (found) return found;
    }
    return null;
  }

  _findAllButtons() {
    let result = [];
    for (const child of this.children) {
      if (child.tagName === 'button') result.push(child);
      if (child._findAllButtons) result = result.concat(child._findAllButtons());
    }
    return result;
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  removeChild(child) {
    const idx = this.children.indexOf(child);
    if (idx >= 0) this.children.splice(idx, 1);
    return child;
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
  }

  getAttribute(name) {
    return this.attributes[name] || null;
  }
}

// ========== 初始化沙箱 ==========
let localStorageData = {};
const watchlistTagsEl = new MockElement('div');
const batchResultsEl = new MockElement('div');
const batchStatusEl = new MockElement('div');
const configSummaryEl = new MockElement('div');
const addStockInputEl = new MockElement('input');
const codeInputEl = new MockElement('input');
const bodyEl = new MockElement('body');

const elements = {
  'watchlistTags': watchlistTagsEl,
  'batchResults': batchResultsEl,
  'batchStatus': batchStatusEl,
  'configSummary': configSummaryEl,
  'addStockInput': addStockInputEl,
  'codeInput': codeInputEl,
};

const documentMock = {
  getElementById: (id) => elements[id] || new MockElement('div'),
  createElement: (tag) => new MockElement(tag),
  body: bodyEl,
  addEventListener: () => {},
  querySelectorAll: () => [],
  querySelector: () => null,
};

const sandbox = {
  console,
  document: documentMock,
  setTimeout: (fn) => { fn(); },
  clearTimeout: () => {},
  Promise,
  JSON,
  Math,
  Date,
  Array,
  Object,
  Set,
  Map,
  String,
  Number,
  Boolean,
  Error,
  TypeError,
  RegExp,
  URLSearchParams,
  btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
  atob: (s) => Buffer.from(s, 'base64').toString('binary'),
  unescape: (s) => s,
  escape: (s) => s,
  encodeURIComponent,
  decodeURIComponent,
  alert: () => {},
  addEventListener: () => {},
  fetch: async () => ({ ok: false, status: 404, json: async () => ({}) }),
  localStorage: {
    getItem: (k) => localStorageData[k] || null,
    setItem: (k, v) => { localStorageData[k] = v; },
    removeItem: (k) => { delete localStorageData[k]; },
  },
};
sandbox.window = sandbox;
sandbox.location = { search: '', href: '' };
vm.createContext(sandbox);

// 加载生产代码
const html = fs.readFileSync(HTML_PATH, 'utf8');
const scriptRegex = /<script[^>]*>([\s\S]*?)<\/script>/g;
let match;
let allJS = '';
while ((match = scriptRegex.exec(html)) !== null) {
  allJS += match[1] + '\n';
}
vm.runInContext(allJS, sandbox);

console.log('\n' + '='.repeat(60));
console.log('FIR-21 DOM/点击级集成测试');
console.log('='.repeat(60));

// ========== 辅助函数 ==========
function getModal() {
  // 模态框是body的最后一个子元素（刚append的）
  return bodyEl.children.length > 0 ? bodyEl.children[bodyEl.children.length - 1] : null;
}

function isModalOpen() {
  return bodyEl.children.length > 0;
}

// ========== 测试主函数 ==========
async function runTests() {

  // 场景1: 渲染watchlist，点击×按钮，模态框出现
  console.log('\n【场景1】点击×按钮 → 自定义确认模态框出现');
  {
    localStorageData = {};
    bodyEl.children = [];
    localStorageData['kline_watchlist_v1'] = JSON.stringify(['002436']);

    // 渲染watchlist tags
    sandbox.renderWatchlistTags();

    // 验证按钮已渲染
    const buttons = watchlistTagsEl.querySelectorAll('button');
    assert(buttons.length >= 2, '应渲染至少2个按钮（×和☁️）');

    // 找到×按钮（第一个button）
    const xButton = buttons[0];
    assert(xButton !== null, '应找到×按钮');

    // 点击×按钮
    const bodyChildrenBefore = bodyEl.children.length;
    xButton.click();

    // 等待异步操作完成
    await new Promise(r => setTimeout(r, 10));

    // 验证模态框已插入到body（body子元素数量增加）
    const bodyChildrenAfter = bodyEl.children.length;
    assert(bodyChildrenAfter > bodyChildrenBefore, `点击×按钮后，body应插入模态框（之前${bodyChildrenBefore}个，之后${bodyChildrenAfter}个）`);

    // 模态框是最后一个子元素
    const modalOverlay = bodyEl.children[bodyEl.children.length - 1];
    assert(modalOverlay !== undefined, '应能获取到模态框元素');

    // 验证模态框包含"确定"和"取消"按钮
    const modalButtons = modalOverlay ? modalOverlay.querySelectorAll('button') : [];
    assert(modalButtons.length === 2, `模态框应包含2个按钮（确定/取消），实际${modalButtons.length}个`);
  }

  // 场景2: 点击Cancel → 股票保留，无tombstone
  console.log('\n【场景2】点击Cancel → 股票保留，无tombstone，无同步');
  {
    localStorageData = {};
    bodyEl.children = [];
    localStorageData['kline_watchlist_v1'] = JSON.stringify(['002436']);

    sandbox.renderWatchlistTags();
    const xButton = watchlistTagsEl.querySelectorAll('button')[0];
    xButton.click();
    await new Promise(r => setTimeout(r, 10));

    // 找到模态框，点击Cancel（第一个按钮）
    const modalOverlay = getModal();
    const cancelBtn = modalOverlay.querySelectorAll('button')[0];
    cancelBtn.click();
    await new Promise(r => setTimeout(r, 10));

    // 验证股票保留
    const watchlist = JSON.parse(localStorageData['kline_watchlist_v1']);
    assertEqual(watchlist, ['002436'], '点击Cancel后，股票应保留');

    // 验证无tombstone
    const tombstones = JSON.parse(localStorageData['kline_watchlist_tombstones_v1'] || '[]');
    assertEqual(tombstones.length, 0, '点击Cancel后，不应创建tombstone');

    // 验证模态框已关闭
    assert(!isModalOpen(), '点击Cancel后，模态框应关闭');
  }

  // 场景3: 点击Confirm → 股票删除，tombstone创建
  console.log('\n【场景3】点击Confirm → 股票删除，tombstone创建');
  {
    localStorageData = {};
    bodyEl.children = [];
    localStorageData['kline_watchlist_v1'] = JSON.stringify(['002436']);

    sandbox.renderWatchlistTags();
    const xButton = watchlistTagsEl.querySelectorAll('button')[0];
    xButton.click();
    await new Promise(r => setTimeout(r, 10));

    // 找到模态框，点击Confirm（第二个按钮）
    const modalOverlay = getModal();
    const confirmBtn = modalOverlay.querySelectorAll('button')[1];
    confirmBtn.click();
    await new Promise(r => setTimeout(r, 10));

    // 验证股票已删除
    const watchlist = JSON.parse(localStorageData['kline_watchlist_v1']);
    assertEqual(watchlist, [], '点击Confirm后，股票应从watchlist移除');

    // 验证tombstone已创建
    const tombstones = JSON.parse(localStorageData['kline_watchlist_tombstones_v1'] || '[]');
    assertEqual(tombstones.length, 1, '点击Confirm后，应创建1条tombstone');
    assertEqual(tombstones[0].code, '002436', 'tombstone code应为002436');

    // 验证模态框已关闭
    assert(!isModalOpen(), '点击Confirm后，模态框应关闭');
  }

  // 场景4: ☁️按钮也调用相同的确认流程
  console.log('\n【场景4】☁️按钮 → 同样触发自定义确认模态框');
  {
    localStorageData = {};
    bodyEl.children = [];
    localStorageData['kline_watchlist_v1'] = JSON.stringify(['600519']);

    sandbox.renderWatchlistTags();
    const cloudButton = watchlistTagsEl.querySelectorAll('button')[1]; // ☁️按钮
    assert(cloudButton !== null, '应找到☁️按钮');

    cloudButton.click();
    await new Promise(r => setTimeout(r, 10));

    // 验证模态框出现
    const modalOverlay = getModal();
    assert(modalOverlay !== undefined, '点击☁️按钮后，自定义确认模态框应出现');

    // 点击Confirm
    const confirmBtn = modalOverlay.querySelectorAll('button')[1];
    confirmBtn.click();
    await new Promise(r => setTimeout(r, 10));

    // 验证股票已删除
    const watchlist = JSON.parse(localStorageData['kline_watchlist_v1']);
    assertEqual(watchlist, [], '点击☁️按钮Confirm后，股票应删除');
  }

  // 场景5: 多只股票时，只删除点击的那只
  console.log('\n【场景5】多只股票 → 只删除点击的那只');
  {
    localStorageData = {};
    bodyEl.children = [];
    localStorageData['kline_watchlist_v1'] = JSON.stringify(['002436', '600519', '002594']);

    sandbox.renderWatchlistTags();

    // 点击第二只股票的×按钮
    // 每只股票有2个按钮，所以第二只股票的×按钮是index=2
    const allButtons = watchlistTagsEl.querySelectorAll('button');
    assert(allButtons.length === 6, '3只股票应渲染6个按钮');

    const secondStockXButton = allButtons[2]; // 第二只股票的×按钮
    secondStockXButton.click();
    await new Promise(r => setTimeout(r, 10));

    const modalOverlay = getModal();
    const confirmBtn = modalOverlay.querySelectorAll('button')[1];
    confirmBtn.click();
    await new Promise(r => setTimeout(r, 10));

    // 验证只删除了第二只股票
    const watchlist = JSON.parse(localStorageData['kline_watchlist_v1']);
    assertEqual(watchlist, ['002436', '002594'], '应只删除点击的那只股票（600519）');
  }

  // ========== 总结 ==========
  console.log('\n' + '='.repeat(60));
  console.log(`测试结果: ${passed} 通过, ${failed} 失败`);
  console.log('='.repeat(60));

  if (failed > 0) {
    console.error('\n失败的测试:');
    failures.forEach((f, i) => console.error(`  ${i+1}. ${f}`));
    process.exit(1);
  }

  console.log('\n🎉 全部DOM/点击级集成测试通过！（FIR-21）');
  process.exit(0);
}

runTests().catch(e => {
  console.error('测试执行出错:', e.message);
  console.error(e.stack);
  process.exit(1);
});
