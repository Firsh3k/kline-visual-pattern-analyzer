# Node BFF + 静态网页镜像。构建上下文为仓库根目录：
#   docker build -f apps/bff/Dockerfile --build-arg RELEASE_VERSION=v6.0.0 \
#     --build-arg RELEASE_COMMIT=$(git rev-parse HEAD) --build-arg RELEASED_AT=$(date -u +%F) \
#     --build-arg RELEASE_ENV=staging -t kline-bff:v6.0.0 .
FROM node:20.18.0-alpine

ENV NODE_ENV=production
WORKDIR /srv
COPY apps/bff/package.json apps/bff/package-lock.json apps/bff/
RUN cd apps/bff && npm ci --omit=dev --no-audit --no-fund && npm cache clean --force
COPY release_identity.js ./
COPY apps/bff/src apps/bff/src
COPY apps/web apps/web
COPY apps/web-v5 apps/web-v5

ARG RELEASE_VERSION=""
ARG RELEASE_COMMIT=""
ARG RELEASED_AT=""
ARG RELEASE_ENV=""
RUN RELEASE_VERSION="$RELEASE_VERSION" RELEASE_COMMIT="$RELEASE_COMMIT" RELEASED_AT="$RELEASED_AT" \
    RELEASE_ENV="$RELEASE_ENV" node apps/bff/src/write-release-info.js /srv/release-info.json
ENV FRONTEND_RELEASE_FILE=/srv/release-info.json STATIC_DIR=/srv/apps/web STATIC_DIR_V5=/srv/apps/web-v5 PORT=8080
LABEL org.opencontainers.image.source="https://github.com/Firsh3k/kline-visual-pattern-analyzer" \
      org.opencontainers.image.revision="${RELEASE_COMMIT}" org.opencontainers.image.version="${RELEASE_VERSION}"

USER node
WORKDIR /srv/apps/bff
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:8080/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "src/server.js"]
