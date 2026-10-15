"""e2e 共用：整个测试会话只启动一个 Playwright（同步 API 不能在同一进程里同时开两个）。"""
import pytest


@pytest.fixture(scope="session")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()
