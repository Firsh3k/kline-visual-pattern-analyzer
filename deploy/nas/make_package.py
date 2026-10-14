"""生成群晖 NAS（Container Manager）可直接导入的 docker-compose.yml（含随机密钥与登录口令）。

用法：python deploy/nas/make_package.py <输出目录> [镜像标签，默认 nas]
输出：<输出目录>/kline/docker-compose.yml 与 <输出目录>/登录信息.txt
镜像来自 GHCR（.github/workflows/publish-images.yml 发布），NAS 不需要构建、不需要访问 Docker Hub。
"""
import secrets
import string
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OWNER = "firsh3k"

TEMPLATE = """# K线形态分析 · 群晖 NAS 部署（Container Manager → 项目 → 新增 → 上传本文件）
# 只对本机开放 8080；外部访问经 DSM 反向代理（HTTPS）进入。数据保存在卷 kline-data，更新镜像不会丢。
name: kline
services:
  analysis:
    image: ghcr.io/{owner}/kline-analysis-api:{tag}
    environment:
      ANALYSIS_API_TOKEN: "{api_token}"
      MARKET_DATA_PROVIDER: live
      DEPLOY_ENV: staging
      SCAN_CONCURRENCY: "2"
      ANALYSIS_CONCURRENCY: "4"
    volumes:
      - kline-data:/data
    networks: [internal]
    mem_limit: 512m
    cpus: 1.5
    restart: unless-stopped
  bff:
    image: ghcr.io/{owner}/kline-bff:{tag}
    environment:
      DEPLOY_ENV: staging
      PYTHON_API_BASE_URL: http://analysis:8000
      PYTHON_API_PRIVATE_NETWORK: "true"
      PYTHON_API_TOKEN: "{api_token}"
      SESSION_SECRET: "{session_secret}"
      LOGIN_USERNAME: "{username}"
      LOGIN_PASSWORD_HASH: "{password_hash}"
      COOKIE_SECURE: "true"
      TRUST_PROXY_HOPS: "1"
    ports:
      - "127.0.0.1:8080:8080"
    depends_on: [analysis]
    networks: [internal, public]
    mem_limit: 256m
    cpus: 1.0
    restart: unless-stopped
volumes:
  kline-data:
networks:
  internal:
  public:
"""


def main() -> None:
    out = Path(sys.argv[1]); tag = sys.argv[2] if len(sys.argv) > 2 else "nas"
    alphabet = string.ascii_letters + string.digits
    password = "".join(secrets.choice(alphabet) for _ in range(14))
    pw_hash = subprocess.run(["node", str(ROOT / "apps/bff/src/hash-password.js")], input=password,
                             capture_output=True, text=True, check=True).stdout.strip()
    yml = TEMPLATE.format(owner=OWNER, tag=tag, api_token=secrets.token_hex(32), session_secret=secrets.token_hex(32),
                          username="owner", password_hash=pw_hash.replace("$", "$$"))  # compose 中 $ 需写成 $$
    (out / "kline").mkdir(parents=True, exist_ok=True)
    (out / "kline" / "docker-compose.yml").write_text(yml, encoding="utf-8")
    (out / "登录信息.txt").write_text(f"用户名：owner\n密码：{password}\n（只保存在你自己这里；忘了就重新生成一份配置）\n",
                                       encoding="utf-8")
    print("ok", out)


if __name__ == "__main__":
    main()
