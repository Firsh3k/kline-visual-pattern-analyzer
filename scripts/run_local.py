#!/usr/bin/env python3
"""
V6 本机一键启动（不需要 Docker，也不需要服务器）

    python scripts/run_local.py          # 只允许本机浏览器访问 http://localhost:8080
    python scripts/run_local.py --lan    # 同一 Wi-Fi 下的手机也能访问

第一次运行会：
  1. 在 var/local/venv 建 Python 虚拟环境并安装分析服务依赖；
  2. 在 apps/bff 安装 Node 依赖；
  3. 生成服务间密钥，并让你设置网页登录密码（至少 12 位），保存在 var/local/secrets.json（不进 Git）；
之后每次运行直接启动 Python 分析服务（127.0.0.1:8000，只对本机开放）和网页（8080 端口），并打开浏览器。
按 Ctrl+C 同时停止两个服务。

行情、同花顺板块成分股都由你这台电脑直接去拉取，所以必须在国内网络下运行。
需要：Python 3.10+、Node.js 20+。
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "var" / "local"
VENV = STATE / "venv"
SECRETS = STATE / "secrets.json"
SERVICE = ROOT / "services" / "analysis_api"
BFF = ROOT / "apps" / "bff"
PY_PORT = 8000


def fail(msg: str) -> None:
    print(f"\n❌ {msg}")
    sys.exit(1)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def check_tools() -> str:
    if sys.version_info < (3, 10):
        fail(f"需要 Python 3.10 或更高版本，当前是 {sys.version.split()[0]}")
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        fail("没有找到 Node.js。请先安装 Node.js 20 或更高版本（https://nodejs.org/zh-cn），装好后重新打开终端再运行。")
    major = int(subprocess.run([node, "-p", "process.versions.node.split('.')[0]"],
                               capture_output=True, text=True, check=True).stdout.strip())
    if major < 20:
        fail(f"需要 Node.js 20 或更高版本，当前是 {major}")
    return npm


def install_python_deps() -> None:
    req = SERVICE / "requirements.txt"
    stamp = STATE / "requirements.sha256"
    digest = hashlib.sha256(req.read_bytes()).hexdigest()
    if venv_python().exists() and stamp.exists() and stamp.read_text() == digest:
        return
    print("📦 安装 Python 依赖（第一次需要一两分钟）…")
    if not venv_python().exists():
        venv.create(VENV, with_pip=True)
    subprocess.run([str(venv_python()), "-m", "pip", "install", "-q", "-r", str(req)], check=True)
    stamp.write_text(digest)


def install_node_deps(npm: str) -> None:
    lock = BFF / "package-lock.json"
    stamp = STATE / "bff-lock.sha256"
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    if (BFF / "node_modules").exists() and stamp.exists() and stamp.read_text() == digest:
        return
    print("📦 安装 Node 依赖…")
    subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=BFF, check=True, shell=(os.name == "nt"))
    stamp.write_text(digest)


def load_secrets() -> dict:
    if SECRETS.exists():
        return json.loads(SECRETS.read_text(encoding="utf-8"))
    print("\n🔐 第一次运行：设置网页登录密码（至少 12 位，输入时不显示）")
    while True:
        pw = getpass.getpass("密码：")
        if len(pw) < 12:
            print("密码至少 12 位，请重新输入")
            continue
        if getpass.getpass("再输一次：") != pw:
            print("两次不一致，请重新输入")
            continue
        break
    h = subprocess.run(["node", str(BFF / "src" / "hash-password.js")], input=pw, capture_output=True,
                       text=True, check=True).stdout.strip()
    data = {"username": "owner", "password_hash": h,
            "analysis_api_token": secrets.token_hex(32), "session_secret": secrets.token_hex(32)}
    SECRETS.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(SECRETS, 0o600)
    except OSError:
        pass
    print("已保存。登录用户名：owner")
    return data


def wait_ready(url: str, proc: subprocess.Popen, name: str, timeout: float = 60) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            fail(f"{name} 启动失败（退出码 {proc.returncode}），请看上面的报错")
        try:
            if urllib.request.urlopen(url, timeout=2).status == 200:
                return
        except Exception:
            time.sleep(0.5)
    fail(f"{name} 在 {timeout:.0f} 秒内没有就绪")


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("223.5.5.5", 53))
            return s.getsockname()[0]
    except OSError:
        return "你的电脑IP"


def main() -> None:
    ap = argparse.ArgumentParser(description="V6 本机一键启动")
    ap.add_argument("--lan", action="store_true", help="允许同一局域网的手机访问")
    ap.add_argument("--port", type=int, default=8080, help="网页端口（默认 8080）")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    STATE.mkdir(parents=True, exist_ok=True)
    npm = check_tools()
    install_python_deps()
    install_node_deps(npm)
    s = load_secrets()

    py_env = {**os.environ, "ANALYSIS_API_TOKEN": s["analysis_api_token"], "ANALYSIS_DATA_DIR": str(STATE / "data"),
              "MARKET_DATA_PROVIDER": "live", "THS_SECTOR_SOURCE": "web", "DEPLOY_ENV": "development"}
    bff_env = {**os.environ, "DEPLOY_ENV": "development", "NODE_ENV": "production",
               "PYTHON_API_BASE_URL": f"http://127.0.0.1:{PY_PORT}", "PYTHON_API_TOKEN": s["analysis_api_token"],
               "SESSION_SECRET": s["session_secret"], "LOGIN_USERNAME": s["username"],
               "LOGIN_PASSWORD_HASH": s["password_hash"], "COOKIE_SECURE": "false", "TRUST_PROXY_HOPS": "0",
               "PORT": str(args.port)}
    if not args.lan:
        bff_env["BIND_HOST"] = "127.0.0.1"

    print("🚀 启动分析服务…")
    py = subprocess.Popen([str(venv_python()), "-m", "uvicorn", "--factory", "app.main:app_factory",
                           "--host", "127.0.0.1", "--port", str(PY_PORT), "--workers", "1"], cwd=SERVICE, env=py_env)
    procs = [py]
    try:
        wait_ready(f"http://127.0.0.1:{PY_PORT}/healthz", py, "分析服务")
        print("🚀 启动网页…")
        bff = subprocess.Popen(["node", "src/server.js"], cwd=BFF, env=bff_env)
        procs.append(bff)
        wait_ready(f"http://127.0.0.1:{args.port}/healthz", bff, "网页服务")
        url = f"http://localhost:{args.port}"
        print(f"\n✅ 已启动：{url}   （用户名 owner，密码是你设置的那个）")
        if args.lan:
            print(f"📱 手机连同一个 Wi-Fi 后打开：http://{lan_ip()}:{args.port}")
        print("按 Ctrl+C 停止。\n")
        if not args.no_browser:
            webbrowser.open(url)
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        fail("有服务意外退出，请看上面的报错")
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
