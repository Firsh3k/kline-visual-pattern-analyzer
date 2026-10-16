"""
V5 界面 · V6 引擎（apps/web-v5）端到端测试（真实浏览器：手机 390×844 触屏 + PC 1366×900）

链路：Python FastAPI（fixture 行情，同一 core）→ Node BFF（令牌登录 / CORS 白名单 / 代理）→ V5 界面。
(a) /v5/ 登录、分析 002436：形态名称 / 分数 / 阶段 / 等级 / 价位与 API 一致；大盘卡片、两张 canvas K 线图渲染；回测 asOf；401 回到登录
(b) 典型图形库（引擎图鉴 + 校准统计）、自选股（服务端 + ETag）添加 / 删除 / 分析全部、板块扫描（同花顺动态板块验证 + 自定义板块）
(c) 跨域：scripts/build_v5_single.py --engine <BFF> 打包单文件，由另一个端口提供；白名单来源可登录分析，非白名单来源给出可见的失败说明
(d) /v5/ 没有浏览器控制台错误、没有 CSP 违规

运行：python -m pytest tests/e2e -q   （需要 playwright + chromium、node、services/analysis_api 依赖；E2E_PYTHON 指定分析服务的 Python）
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PW = "correct-horse-battery"
TOKEN = "e" * 40
TOL = 1e-4

playwright = pytest.importorskip("playwright.sync_api")


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait(url, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"{url} 未就绪")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    data = tmp_path_factory.mktemp("apidata_v5")
    site = tmp_path_factory.mktemp("v5site")           # 单文件版的“外部托管”目录
    api_port, bff_port, site_port, bad_port = _port(), _port(), _port(), _port()
    py = os.environ.get("E2E_PYTHON", sys.executable)
    env = {**os.environ, "ANALYSIS_API_TOKEN": TOKEN, "MARKET_DATA_PROVIDER": "fixture",
           "FIXTURE_DIR": str(ROOT / "services/analysis_api/fixtures"), "ANALYSIS_DATA_DIR": str(data),
           "ENGINE_VERSION": "v6.0.0-rc.1", "ENGINE_COMMIT": "0123456789abcdef0123456789abcdef01234567",
           "BUILD_TIME": "2026-10-14T00:00:00Z", "DEPLOY_ENV": "development"}
    api = subprocess.Popen([py, "-m", "uvicorn", "--factory", "app.main:app_factory", "--port", str(api_port)],
                           cwd=ROOT / "services/analysis_api", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hashed = subprocess.run(["node", "src/hash-password.js"], input=PW, text=True, capture_output=True,
                            cwd=ROOT / "apps/bff", check=True).stdout.strip()
    benv = {**os.environ, "NODE_ENV": "development", "DEPLOY_ENV": "development", "PORT": str(bff_port),
            "PYTHON_API_BASE_URL": f"http://127.0.0.1:{api_port}", "PYTHON_API_TOKEN": TOKEN,
            "SESSION_SECRET": "x" * 48, "LOGIN_USERNAME": "owner", "LOGIN_PASSWORD_HASH": hashed,
            "RATE_SCAN_CREATE_PER_MIN": "100", "RATE_LOGIN_PER_15MIN": "100", "RATE_API_PER_MIN": "2000",
            "CORS_ALLOWED_ORIGINS": f"http://127.0.0.1:{site_port}"}
    bff = subprocess.Popen(["node", "src/server.js"], cwd=ROOT / "apps/bff", env=benv,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{bff_port}"
    # 单文件：一份写入默认引擎地址，一份不写（由用户在“引擎地址”里填写）
    for args, name in ((["--engine", base], "kline-v5.html"), ([], "kline-v5-noengine.html")):
        subprocess.run([sys.executable, str(ROOT / "scripts/build_v5_single.py"), *args, "--out", str(site / name)],
                       check=True, capture_output=True)
    sites = [subprocess.Popen([sys.executable, "-m", "http.server", str(p), "--bind", "127.0.0.1"], cwd=site,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for p in (site_port, bad_port)]
    procs = [bff, api, *sites]
    try:
        _wait(f"http://127.0.0.1:{api_port}/healthz")
        _wait(f"{base}/readyz")
        _wait(f"http://127.0.0.1:{site_port}/kline-v5.html")
        _wait(f"http://127.0.0.1:{bad_port}/kline-v5.html")
        yield {"base": base, "site": f"http://127.0.0.1:{site_port}", "bad_site": f"http://127.0.0.1:{bad_port}"}
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(10)


DEVICES = {
    "phone": dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                  user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"),
    "pc": dict(viewport={"width": 1366, "height": 900}),
}

_CSP_HOOK = """document.addEventListener('securitypolicyviolation', (e) => {
  (window.__csp = window.__csp || []).push(e.violatedDirective + ' ' + e.blockedURI); });"""


# ------------------------------------------------------------------ API 直连（同一 BFF，令牌访问）

class Api:
    def __init__(self, base):
        self.base = base
        self.token = self.call("POST", "/auth/token", {"username": "owner", "password": PW}, auth=False)["token"]

    def call(self, method, path, body=None, auth=True):
        h = {"Accept": "application/json"}
        if body is not None:
            h["Content-Type"] = "application/json"
        if auth:
            h["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base + path, method=method, headers=h,
                                     data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read())

    def analysis(self, code, as_of=None):
        body = {"code": code}
        if as_of:
            body["asOf"] = as_of
        return self.call("POST", "/api/v1/analyses", body)["analysis"]


@pytest.fixture(scope="module")
def api(stack):
    return Api(stack["base"])


def _open(browser, url, device):
    ctx = browser.new_context(**DEVICES[device])
    ctx.add_init_script(_CSP_HOOK)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    # 浏览器对 4xx/5xx 响应自带的“Failed to load resource”日志不算页面错误（错误路径是故意触发的，页面会显示说明）
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "the server responded with a status of" not in m.text else None)
    resp = page.goto(url)
    page._errors = errors
    page._resp = resp
    return ctx, page


def _login(page):
    page.wait_for_selector("#loginForm input[name=username]")
    page.fill("#loginForm input[name=username]", "owner")
    page.fill("#loginForm input[name=password]", PW)
    page.click("#loginSubmit")


def _clean(page):
    """(d) 没有控制台错误、没有 CSP 违规。"""
    assert not page._errors, page._errors
    assert page.evaluate("window.__csp || []") == []


def _no_hscroll(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "页面出现横向滚动"


def _latest(doc):
    """每种形态最近一次（关键点最晚）的结果 —— 与引擎 pattern_summary 同一取法。"""
    out = {}
    for pid, rs in doc["core"]["patterns"].items():
        if rs:
            out[pid] = max(rs, key=lambda x: max(k["index"] for k in x["key_points"]))
    return out


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _num(s):
    return None if s in ("", None) else float(s)


def _assert_stock_matches(page, doc):
    page.wait_for_selector("[data-testid=patterns] tr.prow, [data-testid=patterns] tr[data-absent]")
    assert page.text_content("[data-testid=quote] [data-field=code]") == doc["code"]
    assert page.text_content("[data-testid=quote] [data-field=name]") == doc["name"]
    assert float(page.text_content("[data-testid=quote] [data-field=close]")) == pytest.approx(doc["bars"][-1][4], abs=1e-6)
    rows = page.locator("tr.prow")
    total = sum(len(rs) for rs in doc["core"]["patterns"].values())
    assert rows.count() == total
    absent = set(page.eval_on_selector_all("tr[data-absent]", "els => els.map(e => e.dataset.pid)"))
    assert absent == {pid for pid, rs in doc["core"]["patterns"].items() if not rs}
    shown = page.eval_on_selector_all("tr.prow", """trs => trs.map(t => ({id: t.dataset.id,
        name: t.querySelector('[data-field=name]').textContent, stage: t.querySelector('[data-field=stage]').dataset.v,
        stageText: t.querySelector('[data-field=stage]').textContent, score: Number(t.querySelector('[data-field=score]').textContent),
        conf: t.querySelector('[data-field=confidence]').textContent}))""")
    stage_text = {"bull": {"forming": "构建中", "pre_break": "突破前夜", "breakout_day": "突破当日", "post_break": "已突破",
                           "retest": "回踩确认", "failed_break": "突破失败", "complete": "完成"},
                  "bear": {"forming": "构建中", "pre_break": "跌破前夜", "breakout_day": "跌破当日", "post_break": "已跌破",
                           "retest": "反抽确认", "failed_break": "跌破失败", "complete": "完成"}}
    for r in shown:
        pid, idx = r["id"].split("#")
        x = doc["core"]["patterns"][pid][int(idx)]
        assert (r["name"], r["stage"], r["score"], r["conf"]) == (x["pattern_name"], x["stage"], x["score"], x["confidence"])
        assert r["stageText"] == stage_text[x["direction"]][x["stage"]]
    # 上升 / 下降形态匹配卡：每种形态显示最近一个结果的分数
    latest = _latest(doc)
    for d in ("bull", "bear"):
        items = page.eval_on_selector_all(f"[data-testid={d}-match] .match-item",
                                          "els => els.map(e => [e.dataset.pid, e.dataset.absent || '', (e.querySelector('[data-field=score]') || {}).textContent || ''])")
        assert len(items) == 4
        for pid, absent_flag, score in items:
            if pid in latest:
                assert absent_flag == "" and int(score) == latest[pid]["score"], pid
                assert latest[pid]["direction"] == d
            else:
                assert absent_flag == "1"
    # 逐个点选：详情价位与 API 一致
    for i in range(rows.count()):
        row = rows.nth(i)
        row.click()
        page.wait_for_function("id => document.querySelector('tr.prow.sel') && document.querySelector('tr.prow.sel').dataset.id === id",
                               arg=row.get_attribute("data-id"))
        pid, idx = row.get_attribute("data-id").split("#")
        x = doc["core"]["patterns"][pid][int(idx)]
        assert page.get_attribute("#stageCard [data-field=direction]", "data-v") == x["direction"]
        assert page.get_attribute("#stageCard [data-field=stage]", "data-v") == x["stage"]
        for f in ("neckline_price", "breakout_price", "target_price", "stop_loss"):
            v = _num(page.get_attribute(f"#detail [data-field={f}]", "data-v"))
            assert (v is None) if x[f] is None else abs(v - x[f]) <= TOL, (pid, f, v, x[f])
        kps = page.eval_on_selector_all("#detail [data-testid=keypoints] tbody tr",
                                        "trs => trs.map(t => [t.cells[0].textContent, t.cells[1].textContent, Number(t.cells[2].dataset.v)])")
        assert [(n, d) for n, d, _ in kps] == [(k["name"], k["date"]) for k in x["key_points"]]
        assert all(abs(v - k["price"]) <= TOL for (_, _, v), k in zip(kps, x["key_points"]))
    # 量价 / 均线 / ATR 止损 —— 引擎字段原样显示
    vol = doc["core"]["indicators"]["volume"]
    assert page.text_content("[data-testid=volume] [data-field=volStatus]") == vol["volStatus"]
    assert page.text_content("[data-testid=volume] [data-field=volRatio]") == vol["volRatio"]
    assert page.text_content("[data-testid=volume] [data-field=relation]") == vol["relation"]
    assert float(page.text_content("[data-testid=indicators] [data-field=atr_stop]")) == pytest.approx(
        doc["core"]["indicators"]["atr_stop"]["stop_loss"], abs=0.005)
    for k in ("ma5", "ma10", "ma20", "ma60"):
        assert float(page.text_content(f"[data-testid=indicators] [data-field={k}]")) == pytest.approx(
            doc["core"]["indicators"]["latest"][k], abs=0.005)
    assert page.get_attribute("[data-field=session_state]", "data-v") == doc["session_state"]
    assert (page.locator("[data-field=stale]").count() == 1) == bool(doc["calendar"]["stale"])
    assert page.text_content("[data-field=data_hash]") == doc["input"]["data_hash"]
    assert page.text_content("[data-field=result_sha256]") == doc["core"]["result_sha256"]


def _assert_charts(page):
    for sel in ("#chartFull", "#chart60"):
        info = page.eval_on_selector(sel, """c => { const x = c.getContext('2d'); const d = x.getImageData(0, 0, c.width, c.height).data;
            const colors = new Set(); for (let i = 0; i < d.length; i += 4 * 97) colors.add(d[i] + ',' + d[i + 1] + ',' + d[i + 2]);
            return { w: c.width, h: c.height, colors: colors.size }; }""")
        assert info["w"] > 100 and info["h"] > 100, (sel, info)
        assert info["colors"] > 4, (sel, info)       # 不只是底色：画了 K 线、均线、网格


def _assert_market(page, api, as_of=None):
    m = api.call("GET", "/api/v1/market" + (f"?asOf={as_of}" if as_of else ""))["market_env"]
    page.wait_for_selector("[data-testid=market] [data-field=market_score]")
    assert page.text_content("[data-testid=market] [data-field=market_score]") == f"{m['score']}分"
    assert page.text_content("[data-testid=market] [data-field=market_stage]").startswith(m["stage"])
    assert m["advice"] in page.text_content("[data-testid=market]")


# ------------------------------------------------------------------ (a)(d) /v5/：登录 → 分析 002436 与 API 一致

@pytest.mark.parametrize("device", ["phone", "pc"])
def test_v5_stock_matches_engine(browser, stack, api, device):
    url = stack["base"] + ("/v5/?code=002436" if device == "phone" else "/v5/")
    ctx, page = _open(browser, url, device)
    csp = page._resp.headers.get("content-security-policy", "")
    assert "script-src 'self'" in csp and "style-src 'self'" in csp
    assert page.locator("#enginePanel").is_hidden()                      # /v5/ 默认用本站做引擎
    _login(page)
    page.wait_for_selector("[data-testid=patterns]", timeout=30000)        # 深链接 / 默认 002436 自动分析
    doc = api.analysis("002436")
    _assert_stock_matches(page, doc)
    _assert_market(page, api)
    _assert_charts(page)
    page.wait_for_selector("#calibCard table")
    _no_hscroll(page)
    _clean(page)
    ctx.close()


def test_v5_backtest_deeplink_and_401(browser, stack, api):
    ctx, page = _open(browser, stack["base"] + "/v5/?code=600001", "pc")
    _login(page)
    page.wait_for_selector("[data-testid=patterns]", timeout=30000)
    _assert_stock_matches(page, api.analysis("600001"))                  # 无形态样本：如实显示“未识别”
    assert page.locator("tr[data-absent]").count() >= 4
    # 回测模式 = asOf；后续走势 = 不带 asOf 的引擎行情里截止日之后的 K 线
    page.fill("#codeInput", "002436")
    page.click(".switch")                                                  # 用户点开关
    assert page.is_checked("#backtestMode")
    page.fill("#backtestDate", "2026-08-03")
    page.click("#analyzeBtn")
    page.wait_for_selector("[data-testid=backtest] [data-field=bt_return]", timeout=30000)
    cut = api.analysis("002436", "2026-08-03")
    assert cut["as_of"] == "2026-08-03"
    _assert_stock_matches(page, cut)
    _assert_market(page, api, "2026-08-03")
    full = api.analysis("002436")
    later = [b for b in full["bars"] if b[0] > cut["as_of"]]
    bt = cut["bars"][-1][4]
    assert float(page.text_content("[data-field=bt_close]")) == pytest.approx(bt, abs=0.005)
    assert float(page.text_content("[data-field=bt_latest]")) == pytest.approx(later[-1][4], abs=0.005)
    assert float(page.text_content("[data-field=bt_return]").rstrip("%")) == pytest.approx((later[-1][4] - bt) / bt * 100, abs=0.005)
    assert float(page.text_content("[data-field=bt_max]").rstrip("%")) == pytest.approx((max(b[2] for b in later) / bt - 1) * 100, abs=0.005)
    assert float(page.text_content("[data-field=bt_min]").rstrip("%")) == pytest.approx((min(b[3] for b in later) / bt - 1) * 100, abs=0.005)
    _assert_charts(page)
    # 错误显示 BFF 的 message 与 requestId，不显示任何推测结果
    page.click(".switch")
    assert not page.is_checked("#backtestMode")
    page.fill("#codeInput", "300999")
    page.click("#analyzeBtn")
    page.wait_for_selector("#resultArea .error-msg")
    assert "行情源不可用" in page.text_content("#resultArea .error-msg")
    assert "requestId" in page.text_content("#resultArea .error-msg")
    assert page.locator("[data-testid=patterns]").count() == 0
    # 令牌失效 → 401 → 清除令牌并回到登录
    page.evaluate("""() => { const t = JSON.parse(localStorage.getItem('kl_v5_token')); t.token = 'bad.token';
        localStorage.setItem('kl_v5_token', JSON.stringify(t)); }""")
    page.click(".quick-code[data-code='002436']")
    page.wait_for_selector("#loginPanel:not(.hidden)")
    assert "重新登录" in page.text_content("#loginErr")
    assert page.evaluate("localStorage.getItem('kl_v5_token')") is None
    _login(page)
    page.wait_for_selector("#logoutBtn:not(.hidden)")
    _clean(page)
    ctx.close()


# ------------------------------------------------------------------ (b) 典型图形库 + 自选股

@pytest.mark.parametrize("device", ["phone", "pc"])
def test_v5_catalog(browser, stack, api, device):
    ctx, page = _open(browser, stack["base"] + "/v5/", device)
    _login(page)
    page.wait_for_selector("#logoutBtn:not(.hidden)")
    page.click(".tab[data-tab=patterns]")
    page.wait_for_selector(".pattern-card")
    cat = json.loads(urllib.request.urlopen(stack["base"] + "/data/pattern_catalog.json").read())
    seen = []
    for t in ("bull", "bear"):
        page.click(f".pattern-tab[data-ptype={t}]")
        page.wait_for_function("t => [...document.querySelectorAll('.pattern-card .dir-badge')].every(b => b.classList.contains(t))", arg=t)
        ids = page.eval_on_selector_all(".pattern-card", "cs => cs.map(c => c.dataset.pid)")
        assert ids == [p["id"] for p in cat["items"] if p["direction"] == t]
        for p in cat["items"]:
            if p["direction"] != t:
                continue
            rules = page.eval_on_selector_all(f"#cat-{p['id']} [data-testid=engine-rules] li", "ls => ls.map(l => l.textContent)")
            assert rules == p["engine_rules"]
        seen += ids
        body = page.text_content("#patternGrid")
        assert "可靠性" not in body and "成功率" not in body
    assert len(seen) == 8
    page.click(".pattern-card")
    assert page.locator(".pattern-card.expanded [data-testid=engine-rules]").is_visible()
    # 历史统计与校准数据一致
    cal = api.call("GET", "/api/v1/calibration")
    g = next(x for x in cal["groups"] if x["pattern_type"] == seen[-1] and x["market_regime"] == "all" and x["confirmation_state"] == "all")
    stat = page.text_content(f"#cat-{seen[-1]} [data-testid=calib-stat]")
    if g["applicable"]:
        assert str(g["metrics_by_horizon"]["20"]["sample_size"]) in stat
    else:
        assert "N/A" in stat
    _no_hscroll(page)
    _clean(page)
    ctx.close()


@pytest.mark.parametrize("device", ["phone", "pc"])
def test_v5_watchlist_and_analyze_all(browser, stack, api, device):
    api.call("PATCH", "/api/v1/watchlist", {"ops": [{"opId": f"reset-{device}-{time.time_ns()}", "op": "clear", "at": _now()}]})
    time.sleep(0.05)      # 之后的添加时间晚于清空
    ctx, page = _open(browser, stack["base"] + "/v5/", device)
    _login(page)
    page.wait_for_selector("#logoutBtn:not(.hidden)")
    page.click(".tab[data-tab=watchlist]")
    page.wait_for_selector("#watchlistTags .hint, [data-testid=watchlist]")
    for code in ("002436", "600001"):
        page.fill("#addStockInput", code)
        page.click("#wlAddForm button[type=submit]")
        page.wait_for_selector(f"[data-testid=watchlist] [data-code='{code}']")
    assert [w["code"] for w in api.call("GET", "/api/v1/watchlist")["watchlist"]] == ["002436", "600001"]   # 服务端
    # 另一处修改让本页的 ETag 过期 → 页面带 If-Match 收到 412 后同步最新列表并重新提交
    api.call("PATCH", "/api/v1/watchlist", {"ops": [{"opId": f"other-{device}-{time.time_ns()}", "op": "add", "code": "000002", "at": _now()}]})
    page.click("[data-testid=watchlist] [data-rm='600001']")
    page.wait_for_selector("[data-testid=watchlist] [data-code='600001']", state="detached")
    assert "其他设备更新" in page.text_content("#toast")                    # 走了 412 → 刷新 → 重提
    codes = [w["code"] for w in api.call("GET", "/api/v1/watchlist")["watchlist"]]
    assert "600001" not in codes and "002436" in codes and "000002" in codes
    page.wait_for_selector("[data-testid=watchlist] [data-code='000002']")
    # 分析全部 = 扫描任务 scope {type:'watchlist'}，结果与个股分析同源
    page.click("#wlAnalyzeAll")
    page.wait_for_selector("#wlJob [data-testid=results] tr[data-code='002436']", timeout=30000)
    latest = _latest(api.analysis("002436"))
    chips = page.eval_on_selector_all("#wlJob tr[data-code='002436'] .pchip",
                                      "cs => cs.map(c => [c.dataset.pid, c.dataset.stage, Number(c.dataset.score)])")
    assert {c[0] for c in chips} == set(latest)
    for pid, stage, score in chips:
        assert (stage, score) == (latest[pid]["stage"], latest[pid]["score"])
    assert page.locator("#wlJob [data-testid=failures]").count() == 1      # 000002 含非法 K 线
    _no_hscroll(page)
    page.wait_for_selector("[data-testid=watchlist] [data-code='002436'] .nm")   # 结果带回了股票名称
    page.click("#wlJob tr[data-code='002436'] td:nth-child(3)")
    page.wait_for_selector("#page-analyze.active [data-testid=quote] [data-field=code]")
    page.wait_for_function("() => document.querySelector('[data-testid=quote] [data-field=code]').textContent === '002436'")
    _clean(page)
    ctx.close()


# ------------------------------------------------------------------ (b) 市场扫描：同花顺动态板块 Pending → 验证 → 扫描；自定义板块

def test_v5_sector_scan(browser, stack, api):
    ctx, page = _open(browser, stack["base"] + "/v5/", "pc")
    page.on("dialog", lambda d: d.accept())
    _login(page)
    page.wait_for_selector("#logoutBtn:not(.hidden)")
    page.click(".tab[data-tab=scanner]")
    page.wait_for_selector(".sector-chip")
    presets = api.call("GET", "/api/v1/sectors")["presets"]
    assert page.locator(".sector-chip").count() == len(presets)
    page.fill("#thsSectorCode", "886032")
    page.click("#thsForm button[type=submit]")
    page.wait_for_selector("[data-testid=ths-msg]")
    card = "[data-testid=dynamic-sectors] [data-dyn='886032']"
    page.wait_for_selector(f"{card} [data-field=status][data-v=pending]")
    assert page.text_content(f"{card} [data-field=instruction]") == "帮我验证同花顺板块 886032"
    assert page.is_disabled("#sectorList input[value='ths:886032']")
    # 刷新成分股（fixture 环境没有自动拉取）→ 显示失败原因
    page.click(f"{card} button[data-refresh='886032']")
    page.wait_for_selector(f"{card} [data-out='886032'] .error-msg")
    snap = {"schema_version": "1.0", "source": "ths_ifind", "as_of_date": date.today().isoformat(),
            "sectors": {"测试概念": {"sector_code": "886032", "codes": ["002436", "600001"]}}}
    page.fill(f"[data-snap='886032']", json.dumps(snap, ensure_ascii=False))
    page.click(f"{card} button[data-verify='886032']")
    page.wait_for_selector(f"{card} [data-field=status][data-v=verified]")
    assert not page.is_disabled("#sectorList input[value='ths:886032']")
    page.check("#sectorList input[value='ths:886032']")
    page.select_option("#scanMinScore", "0")
    page.select_option("#scanSignalType", "any")
    page.click("#scanStart")
    page.wait_for_selector("#scanJob .onlyM", timeout=30000)
    page.uncheck("#scanJob .onlyM")
    page.wait_for_selector("#scanJob [data-testid=results] tr[data-code='600001']")
    assert "测试概念" in page.text_content("#scanJob tr[data-code='002436']")      # 板块归属
    latest = _latest(api.analysis("002436"))
    chips = page.eval_on_selector_all("#scanJob tr[data-code='002436'] .pchip", "cs => cs.map(c => [c.dataset.pid, Number(c.dataset.score)])")
    assert {pid: s for pid, s in chips} == {pid: x["score"] for pid, x in latest.items()}
    # 勾选 → 加入自选股（服务端）
    page.check("#scanJob tr[data-code='600001'] .sel-item")
    page.click("#scanAddWatch")
    t0 = time.time()
    while "600001" not in [w["code"] for w in api.call("GET", "/api/v1/watchlist")["watchlist"]]:
        assert time.time() - t0 < 10
        time.sleep(0.2)
    # 自定义板块 + 过滤条件（只看上升、分数 ≥ 60）
    page.fill("#customSectorForm input[name=name]", "测试池")
    page.fill("#customSectorForm input[name=codes]", "002436, 000002 300999")
    page.click("#customSectorForm button[type=submit]")
    page.wait_for_selector("#sectorList input[value='custom:测试池']")
    page.uncheck("#sectorList input[value='ths:886032']")
    page.check("#sectorList input[value='custom:测试池']")
    page.select_option("#scanMinScore", "60")
    page.select_option("#scanSignalType", "bull")
    page.click("#scanStart")
    page.wait_for_selector("#scanJob [data-testid=failures]", timeout=30000)
    fails = page.eval_on_selector_all("#scanJob [data-testid=failures] tbody tr", "trs => trs.map(t => [t.cells[0].textContent, t.cells[1].textContent])")
    assert sorted(fails) == [["000002", "invalid_market_data"], ["300999", "market_data_unavailable"]]
    chips = page.eval_on_selector_all("#scanJob tr[data-code='002436'] .pchip", "cs => cs.map(c => [c.dataset.pid, Number(c.dataset.score)])")
    want = {pid for pid, x in latest.items() if x["direction"] == "bull" and x["score"] >= 60 and x["stage"] != "forming"}
    assert {pid for pid, _ in chips} == want
    _no_hscroll(page)
    _clean(page)
    ctx.close()


# ------------------------------------------------------------------ (c) 跨域：单文件版由其他来源提供

@pytest.mark.parametrize("device", ["phone", "pc"])
def test_v5_single_file_cross_origin(browser, stack, api, device):
    ctx, page = _open(browser, stack["site"] + "/kline-v5.html", device)
    assert page.text_content("#loginEngine") == stack["base"]             # 打包时写入的默认引擎
    _login(page)
    page.wait_for_selector("[data-testid=patterns]", timeout=30000)
    _assert_stock_matches(page, api.analysis("002436"))
    _assert_market(page, api)
    _assert_charts(page)
    page.click(".tab[data-tab=patterns]")                                   # 图鉴数据跨域读取
    page.wait_for_selector(".pattern-card")
    assert page.evaluate("JSON.parse(localStorage.getItem('kl_v5_token')).engine") == stack["base"]
    _no_hscroll(page)
    _clean(page)
    ctx.close()


def test_v5_single_file_engine_setting_and_blocked_origin(browser, stack):
    # 未写入引擎地址：先让用户填“引擎地址”，保存到 localStorage 后登录
    ctx, page = _open(browser, stack["site"] + "/kline-v5-noengine.html", "pc")
    page.wait_for_selector("#enginePanel:not(.hidden)")
    assert page.locator("#loginPanel").is_hidden()
    page.fill("#engineInput", stack["base"] + "/")
    page.click("#engineForm button[type=submit]")
    _login(page)
    page.wait_for_selector("[data-testid=patterns]", timeout=30000)
    assert page.evaluate("localStorage.getItem('kl_v5_engine')") == stack["base"]
    _clean(page)
    ctx.close()
    # 不在 CORS_ALLOWED_ORIGINS 里的来源：浏览器拦截，页面给出可见的说明（不会卡住或空白）
    ctx, page = _open(browser, stack["bad_site"] + "/kline-v5.html", "phone")
    _login(page)
    page.wait_for_selector("#loginErr .error-msg")
    msg = page.text_content("#loginErr .error-msg")
    assert "CORS_ALLOWED_ORIGINS" in msg and stack["bad_site"] in msg
    assert page.locator("#loginErr .error-msg").is_visible()
    assert page.locator("[data-testid=patterns]").count() == 0
    ctx.close()


def test_v5_token_endpoint_rejects_unlisted_origin(stack):
    """令牌访问时，跨域来源必须在白名单里（BFF 侧检查，浏览器之外也成立）。"""
    tok = Api(stack["base"]).token
    req = urllib.request.Request(stack["base"] + "/api/v1/watchlist",
                                 headers={"Authorization": f"Bearer {tok}", "Origin": stack["bad_site"]})
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 403
    req = urllib.request.Request(stack["base"] + "/api/v1/watchlist",
                                 headers={"Authorization": f"Bearer {tok}", "Origin": stack["site"]})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.headers["Access-Control-Allow-Origin"] == stack["site"]
