/**
 * FIR-19 测试工具：从 kline_analyzer.html 提取真实生产函数
 * 确保测试的是生产代码，而不是复制的实现
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const HTML_PATH = path.join(__dirname, '..', 'kline_analyzer.html');

/**
 * 从HTML中提取所有<script>标签中的JS代码
 */
function extractScriptsFromHtml(htmlPath) {
  const html = fs.readFileSync(htmlPath, 'utf8');
  const scriptRegex = /<script[^>]*>([\s\S]*?)<\/script>/g;
  let match;
  let allJS = '';
  while ((match = scriptRegex.exec(html)) !== null) {
    allJS += match[1] + '\n';
  }
  return allJS;
}

/**
 * 创建测试沙箱环境，mock所有DOM依赖
 * 返回 { sandbox, runSync, resetMocks, getCallHistory }
 */
function createTestSandbox() {
  const callHistory = [];
  let mockFetch = null;
  let localStorageData = {};

  const sandbox = {
    console: console,
    setTimeout: (fn, ms) => { /* 同步执行，不等待 */ fn(); },
    Promise: Promise,
    JSON: JSON,
    Math: Math,
    Date: Date,
    Array: Array,
    Object: Object,
    Set: Set,
    Map: Map,
    String: String,
    Number: Number,
    Boolean: Boolean,
    Error: Error,
    RegExp: RegExp,
    parseInt: parseInt,
    parseFloat: parseFloat,
    isNaN: isNaN,
    encodeURIComponent: encodeURIComponent,
    decodeURIComponent: decodeURIComponent,
    escape: escape,
    unescape: unescape,
    atob: (str) => Buffer.from(str, 'base64').toString('binary'),
    btoa: (str) => Buffer.from(str, 'binary').toString('base64'),

    // DOM mocks
    document: {
      getElementById: (id) => {
        callHistory.push({ type: 'getElementById', id });
        const el = {
          value: '',
          textContent: '',
          innerHTML: '',
          style: {},
          dataset: {},
          classList: { add: () => {}, remove: () => {}, toggle: () => {} },
          addEventListener: () => {},
          appendChild: () => {},
          removeChild: () => {},
          setAttribute: () => {},
          getAttribute: () => null,
          querySelector: () => null,
          querySelectorAll: () => [],
          getContext: () => ({
            fillRect: () => {}, strokeRect: () => {}, clearRect: () => {},
            beginPath: () => {}, moveTo: () => {}, lineTo: () => {}, stroke: () => {},
            fill: () => {}, arc: () => {}, fillText: () => {}, measureText: () => ({ width: 10 }),
            save: () => {}, restore: () => {}, translate: () => {}, scale: () => {},
            setLineDash: () => {}, createLinearGradient: () => ({ addColorStop: () => {} }),
          }),
          width: 800,
          height: 400,
        };
        return el;
      },
      createElement: (tag) => ({
        tagName: tag, style: {}, dataset: {}, classList: { add: () => {}, remove: () => {} },
        appendChild: () => {}, setAttribute: () => {}, addEventListener: () => {},
        getContext: () => ({}), width: 800, height: 400,
      }),
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener: () => {},
      body: { appendChild: () => {}, style: {} },
      head: { appendChild: () => {} },
      documentElement: { style: {} },
    },

    window: {
      addEventListener: () => {},
      location: { href: '', hash: '', search: '' },
      localStorage: null, // will be set below
      setTimeout: (fn) => { fn(); return 0; },
      clearTimeout: () => {},
    },

    // localStorage mock
    localStorage: {
      getItem: (key) => {
        callHistory.push({ type: 'localStorage.getItem', key });
        return localStorageData[key] || null;
      },
      setItem: (key, value) => {
        callHistory.push({ type: 'localStorage.setItem', key, value });
        localStorageData[key] = value;
      },
      removeItem: (key) => {
        callHistory.push({ type: 'localStorage.removeItem', key });
        delete localStorageData[key];
      },
    },

    // fetch mock (可被测试覆盖)
    fetch: async (...args) => {
      callHistory.push({ type: 'fetch', args });
      if (mockFetch) return mockFetch(...args);
      throw new Error('fetch not mocked');
    },

    // alert mock
    alert: (msg) => { callHistory.push({ type: 'alert', msg }); },

    // confirm mock (默认返回true)
    confirm: (msg) => { callHistory.push({ type: 'confirm', msg }); return true; },
  };

  // 全局函数mocks
  sandbox.getGithubSettings = () => ({
    username: 'testuser',
    repo: 'testrepo',
    token: 'testtoken',
    branch: 'main',
    path: 'config/watchlist.json',
  });

  sandbox.showSyncStatus = (msg, color) => {
    callHistory.push({ type: 'showSyncStatus', msg, color });
  };

  sandbox.getWatchlist = () => {
    try {
      const data = localStorageData['kline_watchlist_v1'];
      return data ? JSON.parse(data) : [];
    } catch(e) { return []; }
  };

  sandbox.saveWatchlist = (codes) => {
    callHistory.push({ type: 'saveWatchlist', codes });
    localStorageData['kline_watchlist_v1'] = JSON.stringify(codes);
  };

  sandbox.renderWatchlistTags = () => { callHistory.push({ type: 'renderWatchlistTags' }); };
  sandbox.renderWatchlistTable = () => { callHistory.push({ type: 'renderWatchlistTable' }); };
  sandbox.CUSTOM_SECTORS_KEY = 'kline_custom_sectors_v1';

  vm.createContext(sandbox);

  return {
    sandbox,
    callHistory,
    setMockFetch: (fn) => { mockFetch = fn; },
    resetMocks: () => {
      callHistory.length = 0;
      localStorageData = {};
      mockFetch = null;
    },
    getLocalStorage: () => localStorageData,
    setLocalStorage: (data) => { localStorageData = { ...data }; },
    // 在沙箱内执行代码（确保全局引用正确）
    runInSandbox: (code) => vm.runInContext(code, sandbox),
    // 在沙箱内调用async函数
    callAsync: (fnName, ...args) => {
      return vm.runInContext(`(${fnName})(${args.map(a => JSON.stringify(a)).join(',')})`, sandbox);
    },
  };
}

/**
 * 加载生产代码到沙箱
 */
function loadProductionCode(sandbox) {
  const jsCode = extractScriptsFromHtml(HTML_PATH);
  // 只执行函数定义，不执行DOM初始化代码
  // 通过包裹在函数中，只提取我们需要的函数
  const wrappedCode = `
    ${jsCode}
    // 导出需要测试的函数
    if (typeof module !== 'undefined') {
      module.exports = {
        mergeWatchlistsWithTombstones,
        mergeCustomSectors,
        fetchRemoteGithubConfig,
        buildMergedConfig,
        syncToGithub,
        deleteFromAllDevices,
      };
    }
  `;
  try {
    vm.runInContext(wrappedCode, sandbox);
  } catch(e) {
    // DOM初始化代码可能报错，但函数定义应该已经成功
    console.warn('生产代码加载警告（可能是DOM初始化代码）:', e.message);
  }

  // 重要：生产代码可能覆盖了我们的mock函数，这里重新覆盖
  // 这些函数在生产代码中定义，但测试时需要使用mock版本
  sandbox.getGithubSettings = () => ({
    username: 'testuser',
    repo: 'testrepo',
    token: 'testtoken',
    branch: 'main',
    path: 'config/watchlist.json',
  });

  // 从沙箱中提取函数
  return {
    mergeWatchlistsWithTombstones: sandbox.mergeWatchlistsWithTombstones,
    mergeCustomSectors: sandbox.mergeCustomSectors,
    fetchRemoteGithubConfig: sandbox.fetchRemoteGithubConfig,
    buildMergedConfig: sandbox.buildMergedConfig,
    syncToGithub: sandbox.syncToGithub,
    deleteFromAllDevices: sandbox.deleteFromAllDevices,
  };
}

module.exports = {
  extractScriptsFromHtml,
  createTestSandbox,
  loadProductionCode,
};
