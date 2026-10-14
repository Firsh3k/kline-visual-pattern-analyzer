# V6 部署与回滚手册

适用于 V6 的两个服务：**Node BFF + 网页**（`apps/bff`、`apps/web`）和 **Python 分析服务**（`services/analysis_api`，复用仓库根目录 `core/`）。

- 生产切流需要在独立 QA 通过之后、由 PO 单独确认，本手册不包含切流授权。
- 现有的 `v4.0.0` / `v5.0.0` / `v5.0.1` tag 和 release 一律不得移动或覆盖。

## 1. 拓扑

```
手机 / PC ──HTTPS──▶ BFF（App Builder，唯一公开域名）
                      │  会话鉴权 · CSRF · 限流 · 超时 · requestId · 静态网页
                      └──HTTPS（或私有网络 http）+ Bearer 服务凭证──▶ Python 分析服务（不公开）
                                                                         │ core/（8 形态 · 指标 · 大盘）
                                                                         │ SQLite（/data，唯一写入者）
                                                                         └─▶ 腾讯 / 新浪行情（TLS 校验）
```

两个服务的接口版本必须匹配：
- BFF 期望 `api_schema_version = 1.0`、`analysis_schema_version = 3.0`。
- 不匹配时，BFF 的 `/readyz` 返回 503，所有 API 请求返回 `incompatible_engine`，不会降级到其他算法。

## 2. 准备

| 项 | 生成方式 | 放在哪里 |
|---|---|---|
| `ANALYSIS_API_TOKEN`（≥32 字符） | `openssl rand -hex 32` | Python 服务填 `ANALYSIS_API_TOKEN`，BFF 填 `PYTHON_API_TOKEN`，两边必须相同 |
| `SESSION_SECRET`（≥32） | `openssl rand -hex 32` | BFF |
| `LOGIN_USERNAME` | 自定 | BFF |
| `LOGIN_PASSWORD_HASH` | `printf '%s' '密码' \| node apps/bff/src/hash-password.js`（密码至少 12 位） | BFF。值里含 `$`，写进 `.env` 时必须用单引号包起来；平台的环境变量界面直接粘贴即可 |

所有密钥只放在平台的服务端环境变量里，不要提交到 Git，也不要放进前端或 URL。

## 3. 构建产物（标注版本与 SHA）

在仓库根目录、待发布的提交上执行：

```bash
SHA=$(git rev-parse HEAD); VER=v6.0.0-rc.1; DAY=$(date -u +%F); NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker build -f services/analysis_api/Dockerfile \
  --build-arg ENGINE_VERSION=$VER --build-arg ENGINE_COMMIT=$SHA --build-arg BUILD_TIME=$NOW \
  -t kline-analysis-api:$VER .
docker build -f apps/bff/Dockerfile \
  --build-arg RELEASE_VERSION=$VER --build-arg RELEASE_COMMIT=$SHA --build-arg RELEASED_AT=$DAY \
  --build-arg RELEASE_ENV=staging -t kline-bff:$VER .
docker image inspect --format '{{.Id}}' kline-analysis-api:$VER kline-bff:$VER   # 记录 digest
```

- `vX.Y.Z-rc.N` 只能用于 staging / development。生产必须使用正式 tag，否则页面显示 `Version Unknown`（FIR-28）。
- 引擎固定使用 **CPython 3.10**（与 CI 相同）。Python 3.12 起，`sum()` 对浮点数改为补偿求和，会导致末位差异和结果哈希变化（数值差异约 1e-15，远小于容差，但哈希不同）。

### 3.1 App Builder 的 Node 部署

如果平台直接从仓库构建、不使用镜像：

| 项 | 取值 |
|---|---|
| 运行时 | Node 20 |
| 工作目录 | `apps/bff` |
| 安装 | `npm ci --omit=dev` |
| 启动 | `node src/server.js` |
| 端口 | `PORT`（平台注入，默认 8080） |

环境变量：

```
DEPLOY_ENV=staging
PYTHON_API_BASE_URL=https://<python服务域名>
PYTHON_API_TOKEN=...
SESSION_SECRET=...
LOGIN_USERNAME=...
LOGIN_PASSWORD_HASH=...
TRUST_PROXY_HOPS=1            # 平台前面有 1 层 HTTPS 代理
FRONTEND_RELEASE_FILE=        # 可选；不设则读取仓库根目录 release-info.json（模板 → Version Unknown）
```

发布身份的生成方式：在构建步骤执行

```
RELEASE_VERSION=... RELEASE_COMMIT=... RELEASED_AT=... RELEASE_ENV=staging node apps/bff/src/write-release-info.js ../../release-info.json
```

- 公网地址必须使用 `https://`。BFF 拒绝 http（私有网络除外，需显式设置 `PYTHON_API_PRIVATE_NETWORK=true`），也拒绝 `NODE_TLS_REJECT_UNAUTHORIZED=0`。

### 3.2 Python 分析服务托管

任何能运行容器并提供 HTTPS 和持久卷的平台都可以（例如自有 VM + Caddy/Nginx、Fly.io、Render、Railway）。要求：
- 挂载持久卷到 `/data`，存放 SQLite、迁移快照、备份和板块验证存证。
- 出站可访问 `web.ifzq.gtimg.cn`、`qt.gtimg.cn`、`quotes.sina.cn`（HTTPS）。
- **只运行 1 个实例 / 1 个 worker**：扫描任务状态保存在进程内，SQLite 只能有一个写入者。
- 环境变量：

```
ANALYSIS_API_TOKEN=...
MARKET_DATA_PROVIDER=live
DEPLOY_ENV=staging
ANALYSIS_DATA_DIR=/data
```

- 生产环境禁止 `fixture`，服务启动时会拒绝。

### 3.3 自托管一体化（docker compose）

```bash
cp deploy/.env.example deploy/.env   # 填写
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

- BFF 监听 `BFF_PORT`，前面需要加 HTTPS 反向代理。
- Python 服务不发布端口，只能经 BFF 访问。

## 4. 发布顺序（Staging）

1. **备份**：如果已有 V6 数据卷，先复制 `/data/analysis.sqlite3*`。
2. **部署 Python 分析服务**，确认：
   - `GET https://<python>/healthz` 返回 200；
   - `GET https://<python>/readyz` 返回 200，且 `database`、`core_selftest`、`preset_sectors` 均为 true。
3. **迁移自选股**（首次部署，非破坏性）：

   ```bash
   docker exec -it <python容器> python -m app.migrate --source /srv/watchlist.json --dry-run   # 先预演
   docker exec -it <python容器> python -m app.migrate --source /srv/watchlist.json
   ```

   - 源文件为仓库的 `config/watchlist.json`（V5 v1.2 导出格式），需先复制进容器或挂载。
   - 输出包含源文件 sha256、源快照文件名、数据库备份路径，以及对账结果 `problems: []`。
   - 对账失败时自动回滚并返回非 0，库不变。
   - 同一源文件可以重复执行（幂等）。
4. **部署 BFF**，确认：
   - `GET /healthz` 返回 200；
   - `GET /readyz` 返回 200，且 `python: ready`、`schema: compatible`。
5. **冒烟**：

   ```bash
   BASE=https://<staging> USERNAME=owner PASSWORD='***' CODE=603068 ./deploy/smoke.sh
   ```

   - 会打印 requestId、数据截至日期、dataHash、两个版本身份。
6. 在 iPhone Safari、Android Chrome、PC Chrome 上走一遍 QA 清单（`docs/v6/QA_GUIDE.md`）。

## 5. 回滚

V6 与 V5 生产完全独立。V5 页面（`kline_analyzer.html` / `v5.0.1`）在切流前一直保持不动，回滚就是继续使用 V5 地址。

| 场景 | 操作 |
|---|---|
| BFF 新版有问题 | 平台上回滚到上一个部署（或上一个镜像 tag）。BFF 无状态，会话 Cookie 仍然有效 |
| Python 新版有问题 | 部署上一个镜像 tag，数据卷不动。若 schema 不兼容，BFF 会自动拒绝请求（503），需要两边一起回滚 |
| 迁移结果不满意 | 停 Python 服务，用迁移输出的 `db_backup`（`/data/backups/analysis_<时间>.sqlite3`）覆盖 `/data/analysis.sqlite3`，同时删除 `-wal` / `-shm` 文件，再启动 |
| 数据库损坏 | 同上，使用最近的备份。之后可在网页“自选股 → 导出备份”中核对 |
| 生产切流后需要回退 | 把公开域名切回 V5 页面（PO 决定）。V6 数据卷保留，便于复查 |

数据库 schema 版本记录在 `meta.schema_version`（当前为 1），服务启动时如发现不一致会拒绝启动，以免新旧版本混写。

## 6. 运维

- **排障**：每个响应都带 `X-Request-Id`。Node 日志和 Python 日志都是 JSON 行，按 requestId 检索即可串起一次请求。日志不包含凭证。
- **健康**：
  - BFF `/readyz` 会实际探测 Python `/readyz` 和接口版本。
  - Python `/readyz` 做 core 自检，不访问外网。行情可用性用冒烟脚本验证。
- **限流默认值**：
  - API 每用户 240 次/分钟；
  - 分析每用户 60 次/分钟；
  - 创建扫描每用户 6 次/分钟；
  - 登录每 IP 每 15 分钟 10 次。
  - 可通过 `RATE_*` 环境变量调整。
- **超时**：
  - BFF→Python 默认 8 秒，分析请求 30 秒；
  - 扫描每只股票 25 秒（`PER_CODE_TIMEOUT_S`），并发 4（`SCAN_CONCURRENCY`），单次最多 600 只。

## 7. 已知限制 / 外部阻塞

| 项 | 说明 |
|---|---|
| 真实 HTTPS Staging | 需要你在 App Builder（Node）和 Python 托管平台上按本手册部署，并提供 URL；我会用 `deploy/smoke.sh` 和浏览器 E2E 复测。本环境无法访问 Docker Hub，也无法部署到外部平台 |
| 同花顺 iFinD 验证 | 服务端不直连 iFinD。动态板块需要提交权威快照（页面提供可复制的验证指令），不会猜测成分股 |
| 校准统计 | 均线 / 平台的历史统计由校准脚本的简化规则生成，与当前检测器不一致，一律显示 N/A。需要用 V5 检测器重新生成 |
| 单实例 | 扫描任务在进程内运行。服务重启时，未完成的任务会标记为 `interrupted`，需要重新发起 |
