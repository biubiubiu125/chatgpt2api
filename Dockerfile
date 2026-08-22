ARG BUILDPLATFORM
ARG TARGETPLATFORM
ARG TARGETARCH

FROM --platform=$BUILDPLATFORM node:22-alpine AS web-build

WORKDIR /app/web-vue

COPY web-vue/package.json web-vue/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY VERSION /app/VERSION
COPY CHANGELOG.md /app/CHANGELOG.md
COPY web-vue ./
RUN npm run build


FROM --platform=$TARGETPLATFORM node:22-bookworm-slim AS image-upscale-build

WORKDIR /app/scripts/image_upscale

COPY scripts/image_upscale/package.json scripts/image_upscale/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force


FROM python:3.13-slim AS app

ARG TARGETARCH

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    TZ=Asia/Shanghai

LABEL org.opencontainers.image.source="https://github.com/biubiubiu125/chatgpt2api"

WORKDIR /app

# 安装系统依赖
# - git: Git 存储后端需要
# - libpq-dev: PostgreSQL 客户端库
# - gcc: 编译 psycopg2-binary 需要
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libpq-dev \
    gcc \
    openssl \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY main.py ./
COPY config.example.yaml ./
COPY VERSION ./
COPY api ./api
COPY services ./services
COPY utils ./utils
COPY scripts ./scripts
RUN uv sync --frozen --no-dev

COPY --from=image-upscale-build /usr/local/bin/node /usr/local/bin/node
COPY --from=image-upscale-build /app/scripts/image_upscale/node_modules ./scripts/image_upscale/node_modules
COPY --from=web-build /app/web-vue/dist ./web_dist

EXPOSE 80

CMD ["/app/.venv/bin/python", "-m", "scripts.run_uvicorn"]
