"""
V6 端到端测试（真实浏览器：手机 390×844 触屏 + PC 1366×900）

链路：Python FastAPI（fixture 行情，同一 core）→ Node BFF（鉴权 / CSRF / 代理）→ 网页。
断言 FIR-31 §10：Core → API → BFF → 网页 的形态有无、方向、阶段、分数、置信、关键点、颈线、
目标、止损、突破价逐字段一致（价格容差 1e-4）；个股页与扫描结果一致；自选股跨设备同步且删除不复活；
动态板块 Pending → 验证 → 可扫描；错误 fail-closed 并显示 requestId；版本身份。

运行：python3 -m pytest tests/e2e -q   （需要 playwright + chromium、node、services/analysis_api 依赖）
环境变量 E2E_PYTHON 指定运行分析服务的 Python（默认当前解释器）。截图写入 docs/v6/evidence/。
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
EVIDENCE = ROOT / "docs" / "v6" / "evidence"
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


@pytest.fixture(scope="session")
def stack(tmp_path_factory):
    data = tmp_path_factory.mktemp("apidata")
    api_port, bff_port = _port(), _port()
    py = os.environ.get("E2E_PYTHON", sys.executable)
    env = {**os.environ, "ANALYSIS_API_TOKEN": TOKEN, "MARKET_DATA_PROVIDER": "fixture",
           "FIXTURE_DIR": str(ROOT / "services/analysis_api/fixtures"), "ANALYSIS_DATA_DIR": str(data),
           "ENGINE_VERSION": "v6.0.0-rc.1", "ENGINE_COMMIT": "0123456789abcdef0123456789abcdef01234567",
           "BUILD_TIME": "2026-10-14T00:00:00Z", "DEPLOY_ENV": "development"}
    api = subprocess.Popen([py, "-m", "uvicorn", "--factory", "app.main:app_factory", "--port", str(api_port)],
                           cwd=ROOT / "services/analysis_api", env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    hashed = subprocess.run(["node", "src/hash-password.js"], input=PW, text=True, capture_output=True,
                            cwd=ROOT / "apps/bff", check=True).stdout.strip()
    benv = {**os.environ, "NODE_ENV": "development", "DEPLOY_ENV": "development", "PORT": str(bff_port),
            "PYTHON_API_BASE_URL": f"http://127.0.0.1:{api_port}", "PYTHON_API_TOKEN": TOKEN,
            "SESSION_SECRET": "x" * 48, "LOGIN_USERNAME": "owner", "LOGIN_PASSWORD_HASH": hashed,
            "RATE_SCAN_CREATE_PER_MIN": "100", "RATE_LOGIN_PER_15MIN": "100"}
    bff = subprocess.Popen(["node", "src/server.js"], cwd=ROOT / "apps/bff", env=benv,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        _wait(f"http://127.0.0.1:{api_port}/healthz")
        _wait(f"http://127.0.0.1:{bff_port}/readyz")
        yield {"base": f"http://127.0.0.1:{bff_port}", "api": f"http://127.0.0.1:{api_port}"}
    finally:
        bff.terminate()
        api.terminate()
        bff.wait(10)
        api.wait(10)


DEVICES = {
    "phone": dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                  user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"),
    "pc": dict(viewport={"width": 1366, "height": 900}),
}


@pytest.fixture(scope="session")
def browser():
    with playwright.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _login(browser, stack, device):
    ctx = browser.new_context(**DEVICES[device])
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "status of 4" not in m.text and "status of 5" not in m.text else None)
    page.goto(stack["base"] + "/")
    page.fill("input[name=username]", "owner")
    page.fill("input[name=password]", PW)
    page.click("button[type=submit]")
    page.wait_for_selector("#stockForm")
    page._errors = errors
    return ctx, page


def _shot(page, name):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(EVIDENCE / f"{name}.png"), full_page=True)


def _no_hscroll(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "页面出现横向滚动"


_DIRECT = r"""
import csv, json, sys
sys.path.insert(0, sys.argv[1])
from core import analysis as A
root, code = sys.argv[1], sys.argv[2]
fx = root + "/services/analysis_api/fixtures/"
m = json.load(open(fx + "manifest.json", encoding="utf-8"))["series"][code]
bars = [{"day": r["date"], **{k: float(r[k]) for k in ("open", "high", "low", "close", "volume")}}
        for r in csv.DictReader(open(fx + m["file"], newline="", encoding="utf-8"))]
doc = A.analyze(code, bars, adjustment="qfq", provenance={"source": m["source"]}, name=m["name"])
print(json.dumps({"doc": doc, "summary": A.pattern_summary(doc)}, ensure_ascii=False))
"""


def _direct(code):
    """同一 fixture 输入、同一 Python 运行时直接调用 core（真源）。
    注意：必须与分析服务使用同一 CPython 版本——3.12 起 sum() 对浮点改为补偿求和，末位差异会改变结果哈希。"""
    py = os.environ.get("E2E_PYTHON", sys.executable)
    out = subprocess.run([py, "-c", _DIRECT, str(ROOT), code], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _api_direct(code):
    return _direct(code)["doc"]


def _num(s):
    return None if s in ("", None) else float(s)


# ------------------------------------------------------------------ 个股页：Core → API → BFF → 网页 逐字段一致

@pytest.mark.parametrize("device", ["phone", "pc"])
def test_stock_page_parity_with_core(browser, stack, device):
    ctx, page = _login(browser, stack, device)
    page.goto(stack["base"] + "/#/stock/002436")
    page.wait_for_selector("[data-testid=patterns]")
    direct = _api_direct("002436")
    core_list = []
    for pid, rs in direct["core"]["patterns"].items():
        core_list += rs
    rows = page.locator("tr.prow")
    assert rows.count() == len(core_list)
    # 有无：未识别行恰好对应 core 中结果为空的形态
    absent = set(page.eval_on_selector_all("tr[data-absent]", "els => els.map(e => e.dataset.pid)"))
    assert absent == {pid for pid, rs in direct["core"]["patterns"].items() if not rs}
    assert page.text_content("[data-field=data_hash]") == direct["input"]["data_hash"]
    assert page.text_content("[data-field=result_sha256]") == direct["core"]["result_sha256"]
    seen = []
    for i in range(rows.count()):
        row = rows.nth(i)
        if "sel" in (row.get_attribute("class") or ""):
            row.click()      # 取消选择
        row.click()
        page.wait_for_selector("#detail [data-testid=levels]")
        pid = row.get_attribute("data-pid")
        idx = int(row.get_attribute("data-id").split("#")[1])
        x = direct["core"]["patterns"][pid][idx]
        assert row.locator("[data-field=stage]").get_attribute("data-v") == x["stage"]
        assert int(row.locator("[data-field=score]").text_content()) == x["score"]
        assert row.locator("[data-field=confidence]").text_content() == x["confidence"]
        assert page.get_attribute("#detail [data-field=direction]", "data-v") == x["direction"]
        for f in ("neckline_price", "breakout_price", "target_price", "stop_loss"):
            v = _num(page.get_attribute(f"#detail [data-field={f}]", "data-v"))
            if x[f] is None:
                assert v is None, f
            else:
                assert abs(v - x[f]) <= TOL, (pid, f, v, x[f])
            shown = page.text_content(f"#detail [data-field={f}]")
            if x[f] is not None:
                assert abs(float(shown) - x[f]) <= TOL, (pid, f, shown)        # 显示值同样在容差内
        kps = page.eval_on_selector_all("#detail [data-testid=keypoints] tbody tr",
                                        "trs => trs.map(t => [t.cells[0].textContent, t.cells[1].textContent, Number(t.cells[2].dataset.v), Number(t.cells[2].textContent)])")
        assert len(kps) == len(x["key_points"])
        for (name, d, v, shown), k in zip(kps, x["key_points"]):
            assert name == k["name"] and d == k["date"] and abs(v - k["price"]) <= TOL and abs(shown - k["price"]) <= TOL
        seen.append(pid)
    assert set(seen) == {x["pattern_id"] for x in core_list}
    _no_hscroll(page)
    _shot(page, f"{device}_01_stock_002436")
    assert not page._errors, page._errors
    ctx.close()


@pytest.mark.parametrize("device", ["phone", "pc"])
def test_stock_errors_fail_closed_with_request_id(browser, stack, device):
    ctx, page = _login(browser, stack, device)
    for code, text in (("300999", "行情源不可用"), ("000002", "行情数据不合法"), ("920001", "没有"), ("600001", None)):
        page.goto(stack["base"] + f"/#/stock/{code}")
        if text:
            page.wait_for_selector(".alert.bad")
            assert text in page.text_content(".alert.bad")
            assert "requestId" in page.text_content(".alert.bad")
            assert page.locator("[data-testid=patterns]").count() == 0          # 不显示任何推测结果
        else:   # 无形态样本：如实显示“未识别”
            page.wait_for_selector("[data-testid=patterns]")
            assert page.locator("tr[data-absent]").count() >= 4
    page.goto(stack["base"] + "/#/stock/300999")
    page.wait_for_selector(".alert.bad")
    _shot(page, f"{device}_02_error_unavailable")
    ctx.close()


# ------------------------------------------------------------------ 自选股：跨设备同步、删除不复活

def test_watchlist_cross_device_no_resurrection(browser, stack):
    ctx_a, a = _login(browser, stack, "phone")
    ctx_b, b = _login(browser, stack, "pc")
    a.goto(stack["base"] + "/#/watchlist")
    a.wait_for_selector("#wlAdd")
    for code in ("002436", "600519"):
        a.fill("#wlAdd input", code)
        a.click("#wlAdd button")
        a.wait_for_selector(f"[data-testid=watchlist] [data-code='{code}']")
    b.goto(stack["base"] + "/#/watchlist")
    b.wait_for_selector("[data-testid=watchlist] [data-code='600519']")       # 另一台设备看到同步结果
    b.on("dialog", lambda d: d.accept())
    b.click("[data-rm='600519']")
    b.wait_for_selector("[data-testid=watchlist] [data-code='600519']", state="detached")
    # 设备 A 之前离线时排队的旧“添加”（时间早于删除）现在才同步 → 不得复活
    r = a.evaluate("""async () => { const csrf = document.cookie.match(/kl_csrf=([^;]+)/)[1];
        const r = await fetch('/api/v1/watchlist', {method:'PATCH', headers:{'content-type':'application/json','x-csrf-token':decodeURIComponent(csrf)},
          body: JSON.stringify({ops:[{opId:'offline-000001', op:'add', code:'600519', at:'2020-01-01T00:00:00Z'}]})});
        return await r.json(); }""")
    assert r["results"][0] == {"opId": "offline-000001", "status": "ignored", "reason": "deleted_later"}
    a.goto(stack["base"] + "/#/stock")
    a.goto(stack["base"] + "/#/watchlist")
    a.wait_for_selector("[data-testid=watchlist] [data-code='002436']")
    assert a.locator("[data-testid=watchlist] [data-code='600519']").count() == 0
    _no_hscroll(a)
    _shot(a, "phone_03_watchlist")
    # 批量分析全部 → 与个股页同源
    a.click("#wlScan")
    a.wait_for_selector("[data-testid=results] [data-code='002436']", timeout=30000)
    _shot(a, "phone_04_watchlist_batch")
    # 详情跳转
    a.click("[data-testid=results] [data-code='002436'] a.t")
    a.wait_for_selector("[data-testid=patterns]")
    ctx_a.close()
    ctx_b.close()


# ------------------------------------------------------------------ 市场扫描：进度 / 结果 / 失败清单 / 与个股一致

@pytest.mark.parametrize("device", ["phone", "pc"])
def test_scan_custom_pool(browser, stack, device):
    ctx, page = _login(browser, stack, device)
    page.goto(stack["base"] + "/#/scan")
    page.wait_for_selector("#scanForm")
    page.check("input[name=scope][value=codes]")
    page.fill("textarea[name=codes]", "002436, 002436 600001 000002 300999")
    page.fill("input[name=minScore]", "0")
    page.click("#scanForm button[type=submit]")
    page.wait_for_selector("[data-testid=failures]", timeout=30000)
    page.uncheck("#onlyM")
    page.wait_for_selector("[data-testid=results] [data-code='002436']")
    fails = page.eval_on_selector_all("[data-testid=failures] tbody tr", "trs => trs.map(t => [t.cells[0].textContent, t.cells[1].textContent])")
    assert sorted(fails) == [["000002", "invalid_market_data"], ["300999", "market_data_unavailable"]]
    assert "去重前" in page.text_content("#jobText") or "4/4" in page.text_content("#jobText")
    # 扫描卡片中的形态（名称 / 阶段 / 分数）与个股分析的最近结果一致
    summary = _direct("002436")["summary"]
    chips = page.eval_on_selector_all("[data-testid=results] [data-code='002436'] .chip[data-pid]",
                                      "cs => cs.map(c => [c.dataset.pid, c.textContent])")
    assert {c[0] for c in chips} == set(summary["patterns_latest"])
    for pid, txt in chips:
        assert str(summary["patterns_latest"][pid]["score"]) in txt
    _no_hscroll(page)
    _shot(page, f"{device}_05_scan_results")
    ctx.close()


# ------------------------------------------------------------------ 同花顺动态板块：Pending → 验证 → Verified+Fresh → 可扫描

def test_dynamic_sector_flow(browser, stack):
    ctx, page = _login(browser, stack, "phone")
    page.goto(stack["base"] + "/#/sectors")
    page.wait_for_selector("#dynAdd")
    page.fill("#dynAdd input", "886032")
    page.click("#dynAdd button")
    page.wait_for_selector("[data-dyn='886032'] [data-field=status][data-v=pending]")
    assert page.text_content("[data-dyn='886032'] [data-field=instruction]") == "帮我验证同花顺板块 886032"
    _shot(page, "phone_06_sector_pending")
    # Pending 时扫描页该板块不可选
    page.goto(stack["base"] + "/#/scan")
    page.wait_for_selector("#scanForm")
    assert page.is_disabled("input[name=sec][value='ths:886032']")
    page.goto(stack["base"] + "/#/sectors")
    page.wait_for_selector("[data-snap='886032']")
    snap = {"schema_version": "1.0", "source": "ths_ifind", "as_of_date": date.today().isoformat(),
            "sectors": {"测试概念": {"sector_code": "886032", "codes": ["002436", "600001"]}}}
    page.fill("[data-snap='886032']", json.dumps(snap, ensure_ascii=False))
    page.click("[data-verify='886032']")
    page.wait_for_selector("[data-dyn='886032'] [data-field=status][data-v=verified]")
    _shot(page, "phone_07_sector_verified")
    page.goto(stack["base"] + "/#/scan")
    page.wait_for_selector("#scanForm")
    page.check("input[name=sec][value='ths:886032']")
    page.fill("input[name=minScore]", "0")
    page.click("#scanForm button[type=submit]")
    page.wait_for_selector("#onlyM", timeout=30000)
    page.uncheck("#onlyM")
    page.wait_for_selector("[data-testid=results] [data-code='600001']")
    assert "测试概念" in page.text_content("[data-testid=results] [data-code='002436']")   # 板块归属
    ctx.close()


# ------------------------------------------------------------------ 图鉴 / 版本

@pytest.mark.parametrize("device", ["phone", "pc"])
def test_catalog_and_version(browser, stack, device):
    ctx, page = _login(browser, stack, device)
    page.goto(stack["base"] + "/#/catalog")
    page.wait_for_selector(".catalog article")
    assert page.locator(".catalog article").count() == 8
    body = page.text_content(".catalog")
    assert "N/A" in body and "可靠性" not in body
    assert page.locator("[data-testid=engine-rules]").count() == 8
    assert "右底不低于左底 97%" in body and "创新低则" not in body          # P6：与引擎矛盾的旧说法已更正
    _no_hscroll(page)
    _shot(page, f"{device}_08_catalog")
    page.goto(stack["base"] + "/#/about")
    page.wait_for_selector("[data-testid=engine-version]")
    assert page.text_content("[data-field=fe_version]") == "Version Unknown"          # 模板 release-info → Unknown
    assert page.text_content("[data-field=en_version]") == "v6.0.0-rc.1"
    _shot(page, f"{device}_09_version")
    assert not page._errors, page._errors
    ctx.close()


def test_unauthenticated_cannot_reach_api(stack):
    req = urllib.request.Request(stack["base"] + "/api/v1/watchlist")
    try:
        urllib.request.urlopen(req, timeout=5)
        raise AssertionError("应返回 401")
    except urllib.error.HTTPError as e:
        assert e.code == 401
    req = urllib.request.Request(stack["api"] + "/api/v1/watchlist")
    try:
        urllib.request.urlopen(req, timeout=5)
        raise AssertionError("Python 服务应拒绝无凭证请求")
    except urllib.error.HTTPError as e:
        assert e.code == 401
