ARG BUILDPLATFORM
ARG TARGETPLATFORM
ARG TARGETARCH

FROM --platform=$BUILDPLATFORM docker.io/library/node@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS web-build

WORKDIR /app/web-vue

COPY web-vue/package.json web-vue/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY VERSION /app/VERSION
COPY CHANGELOG.md /app/CHANGELOG.md
COPY web-vue ./
RUN npm run build


FROM --platform=$TARGETPLATFORM docker.io/library/node@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS image-upscale-build

WORKDIR /app/scripts/image_upscale

COPY scripts/image_upscale/package.json scripts/image_upscale/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force


FROM docker.io/library/python@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS app

ARG TARGETARCH
ARG UV_VERSION=0.8.17

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    TZ=Asia/Shanghai

ENV PORT=3000

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

RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}"

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

RUN groupadd --system --gid 10001 chatgpt2api \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app --no-create-home chatgpt2api \
    && mkdir -p /app/data \
    && chown -R 10001:10001 /app/data

USER chatgpt2api

EXPOSE 3000

CMD ["/app/.venv/bin/python", "-m", "scripts.run_uvicorn"]
