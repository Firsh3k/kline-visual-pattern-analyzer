/**
 * 把【实际部署的】V4 页面（v6/app-builder/client/public/v4/index.html）的内联脚本加载进 Node vm，
 * 提供最小 DOM / canvas / fetch 桩。测试直接调用页面里的生产函数（不复制、不改写）。
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..', '..');
// V4_PAGE 仅用于本地验证“测试能在旧实现上失败”，CI 不设置
const PAGE = process.env.V4_PAGE || path.join(ROOT, 'v6', 'app-builder', 'client', 'public', 'v4', 'index.html');

function recordingCanvas() {
  const calls = [];
  let dash = [];
  const ctx = new Proxy({
    setLineDash(d) { dash = d.slice(); calls.push({ op: 'setLineDash', args: [d.slice()] }); },
  }, {
    get(t, k) {
      if (k in t) return t[k];
      if (k === 'calls') return calls;
      return (...args) => calls.push({ op: k, args, dash: dash.slice(), strokeStyle: t.strokeStyle });
    },
    set(t, k, v) { t[k] = v; calls.push({ op: 'set:' + String(k), args: [v] }); return true; },
  });
  return {
    width: 0, height: 0, style: {},
    getBoundingClientRect: () => ({ width: 800, height: 320, left: 0, top: 0 }),
    getContext: () => ctx,
    ctx, calls,
  };
}

function fakeElement(id) {
  const el = {
    id, style: {}, dataset: {}, value: '', checked: false, disabled: false, textContent: '',
    innerHTML: '', className: '', children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {}, removeEventListener() {}, appendChild(c) { this.children.push(c); return c; },
    removeChild() {}, setAttribute() {}, getAttribute: () => null, querySelector: () => fakeElement(),
    querySelectorAll: () => [], focus() {}, blur() {}, click() {}, scrollIntoView() {},
    getBoundingClientRect: () => ({ width: 800, height: 320, left: 0, top: 0 }),
    getContext: () => recordingCanvas().ctx,
  };
  return el;
}

function loadPage(opts = {}) {
  const html = fs.readFileSync(PAGE, 'utf8');
  const scripts = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  if (!scripts.length) throw new Error('页面没有内联脚本');
  const elements = {};
  const document = {
    cookie: '',
    getElementById: id => {
      if (!elements[id]) elements[id] = (opts.canvasIds || []).includes(id) ? recordingCanvas() : fakeElement(id);
      return elements[id];
    },
    querySelector: () => fakeElement(), querySelectorAll: () => [],
    createElement: tag => fakeElement(tag), createTextNode: t => ({ t }),
    addEventListener() {}, removeEventListener() {},
    body: fakeElement('body'), head: fakeElement('head'), documentElement: fakeElement('html'),
    readyState: 'loading',
  };
  const storage = {};
  const localStorage = {
    getItem: k => (k in storage ? storage[k] : null), setItem: (k, v) => { storage[k] = String(v); },
    removeItem: k => { delete storage[k]; }, clear: () => { for (const k in storage) delete storage[k]; },
  };
  const timeouts = [];
  const sandbox = {
    console: opts.quiet === false ? console : { log() {}, warn() {}, error() {}, info() {} },
    document, localStorage, sessionStorage: localStorage,
    navigator: { userAgent: 'node', clipboard: { writeText: async () => {} } },
    location: { href: 'https://example.test/v4/', pathname: '/v4/', search: '', hash: '', origin: 'https://example.test', protocol: 'https:', host: 'example.test' },
    history: { pushState() {}, replaceState() {} },
    alert() {}, confirm: () => false, prompt: () => null,
    setTimeout: (fn) => { timeouts.push(fn); return timeouts.length; }, clearTimeout() {},
    setInterval: () => 0, clearInterval() {}, requestAnimationFrame: () => 0,
    fetch: opts.fetch || (async () => { throw new Error('fetch 未桩'); }),
    URL, URLSearchParams, TextEncoder, TextDecoder, Intl, Date, Math, JSON, Promise, Set, Map,
    devicePixelRatio: 1, addEventListener() {}, removeEventListener() {},
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    atob: s => Buffer.from(s, 'base64').toString('binary'), btoa: s => Buffer.from(s, 'binary').toString('base64'),
  };
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  vm.createContext(sandbox);
  // 顶层 const/let/class 不会挂到全局对象上，这里把需要测试的生产函数显式导出
  const exportNames = opts.exports || [];
  const tail = '\n;globalThis.__exports = {' + exportNames.map(n => `${n}: typeof ${n} !== 'undefined' ? ${n} : undefined`).join(',') + '};';
  vm.runInContext(scripts.join('\n;\n') + tail, sandbox, { filename: 'v4/index.html' });
  return { sandbox, x: sandbox.__exports, elements, timeouts, recordingCanvas };
}

module.exports = { loadPage, recordingCanvas, PAGE, ROOT };
