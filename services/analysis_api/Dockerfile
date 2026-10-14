# Python 分析服务镜像。构建上下文为仓库根目录：
#   docker build -f services/analysis_api/Dockerfile \
#     --build-arg ENGINE_VERSION=v6.0.0-rc.1 --build-arg ENGINE_COMMIT=$(git rev-parse HEAD) \
#     --build-arg BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ) -t kline-analysis-api:v6.0.0-rc.1 .
FROM python:3.10.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /data && chown app:app /data

WORKDIR /srv
COPY services/analysis_api/requirements.txt services/analysis_api/requirements.txt
RUN pip install -r services/analysis_api/requirements.txt

# 只复制运行所需内容；算法源码直接使用仓库 core/（不复制、不改写）
COPY core core
COPY config/preset_sectors.json config/preset_sectors.json
COPY sector_data/provenance sector_data/provenance
COPY data/calibration/output/calibration_summary.json data/calibration/output/calibration_summary.json
COPY tests/v5/fixtures/002436_qfq_20261013.csv tests/v5/fixtures/sh000001_20261013.csv tests/v5/fixtures/
COPY services/analysis_api/app services/analysis_api/app
COPY services/analysis_api/fixtures services/analysis_api/fixtures

ARG ENGINE_VERSION=""
ARG ENGINE_COMMIT=""
ARG BUILD_TIME=""
ENV ENGINE_VERSION=${ENGINE_VERSION} ENGINE_COMMIT=${ENGINE_COMMIT} BUILD_TIME=${BUILD_TIME} \
    ANALYSIS_DATA_DIR=/data MARKET_DATA_PROVIDER=live DEPLOY_ENV=staging
LABEL org.opencontainers.image.source="https://github.com/Firsh3k/kline-visual-pattern-analyzer" \
      org.opencontainers.image.revision="${ENGINE_COMMIT}" org.opencontainers.image.version="${ENGINE_VERSION}"

USER app
WORKDIR /srv/services/analysis_api
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"
# 单进程：扫描任务状态在进程内、SQLite 单写者（见 docs/v6/RUNBOOK.md）
CMD ["uvicorn", "--factory", "app.main:app_factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-keep-alive", "15"]
