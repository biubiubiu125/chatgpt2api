# 部署与升级指南

本文介绍 ChatGPT2API 的一键安装、Docker Compose、主从集群、源码运行和升级回滚。生产部署优先使用 GHCR（GitHub Container Registry）镜像；二次开发或需要本地改代码时再使用源码构建。

## 部署前准备

服务器需要安装：

- Docker Engine
- Docker Compose v2
- `curl`、`bash`
- 主从集群额外需要 WireGuard、`iproute2`、`openssl`

首次部署前建议确认：

```bash
docker version
docker compose version
curl --version
```

项目核心持久化文件：

| 路径 | 作用 |
| --- | --- |
| `data/config.json`（Docker）/ `config.json`（源码） | 主配置、后台密钥、代理、图片、备份等配置 |
| `.env` | Docker Compose 环境变量和部署参数 |
| `data/` | 账号、注册配置、日志、图片、任务记录等运行数据 |
| `join/` | 主从集群一次性 Worker 加入文件，不应提交到 Git |

升级和迁移时重点保留 `.env`、配置文件和 `data/`。标准 Docker Compose 不自动创建外部 PostgreSQL；新配置默认使用 PostgreSQL，`DATABASE_URL` 和 `IMAGE_QUEUE_DATABASE_URL` 必须分别指向可连接且允许应用用户建表的账号库和图片队列数据库。

使用外部 PostgreSQL 时，应用用户需要对 `chatgpt2api_app` 和 `chatgpt2api_image_queue` 具备连接权限，并能在各自数据库的 `public` schema 创建表、序列和索引。管理员可以按实际用户名执行：

```sql
GRANT CONNECT ON DATABASE chatgpt2api_app TO chatgpt2api_runtime;
GRANT CONNECT ON DATABASE chatgpt2api_image_queue TO chatgpt2api_runtime;

\connect chatgpt2api_app
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO chatgpt2api_runtime;

\connect chatgpt2api_image_queue
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO chatgpt2api_runtime;
```

如果账号存储使用 `postgres`，`DATABASE_URL` 必须连接 `chatgpt2api_app`；持久图片队列始终使用 `IMAGE_QUEUE_DATABASE_URL` 连接 `chatgpt2api_image_queue`。启动前的 `database-init` 会创建并校验数据库角色标记，权限不足时会阻止应用启动，而不是让图片接口以半可用状态上线。

## 方式一：一键安装脚本

脚本地址：

```text
deploy/install.sh
```

脚本支持 Docker 镜像模式、源码 Python 模式、WARP 模式以及主从集群命令。交互式安装首先选择单机端、主控/监控端或生图端 Worker；新安装统一使用 PostgreSQL。管理员密钥必须手动填写，安装完成时会显示管理员密钥、PostgreSQL `DATABASE_URL` 和图片队列数据库地址。

### 交互式安装

```bash
curl -fsSL https://raw.githubusercontent.com/biubiubiu125/chatgpt2api/main/deploy/install.sh -o /tmp/chatgpt2api-install.sh
sudo bash /tmp/chatgpt2api-install.sh
```

默认值：

- 安装目录：`/opt/chatgpt2api`
- Docker 端口：`3000`
- 部署端：先选择单机端、主控/监控端或生图端 Worker
- 账号存储：`postgres`
- PostgreSQL 账号库和图片队列：必须分别填写 `DATABASE_URL` 和 `IMAGE_QUEUE_DATABASE_URL`
- 镜像：`ghcr.io/biubiubiu125/chatgpt2api@sha256:c70f118780c9b6e194353b09e8530e20eeed2496cddf9f80ee36c41775178f0a`

### 非交互式安装

适合自动化部署。将下面的管理员密钥和两个 PostgreSQL 地址替换成真实值：

```bash
curl -fsSL https://raw.githubusercontent.com/biubiubiu125/chatgpt2api/main/deploy/install.sh -o /tmp/chatgpt2api-install.sh
  sudo env NONINTERACTIVE=1 MODE=docker INSTALL_DIR=/opt/chatgpt2api PORT=3000 \
  CHATGPT2API_INSTALL_TARGET=standalone \
  CHATGPT2API_AUTH_KEY='replace-with-a-manual-admin-key' \
  DATABASE_URL='postgresql+psycopg2://user:password@postgres-host:5432/chatgpt2api_app' \
  CHATGPT2API_BASE_URL='https://api.example.com' \
  IMAGE_QUEUE_DATABASE_URL='postgresql+psycopg2://user:password@postgres-host:5432/chatgpt2api_image_queue' \
  bash /tmp/chatgpt2api-install.sh --non-interactive
```

交互式安装顺序为：选择语言 → 选择部署端 → 填写对应配置 → 显示安装摘要 → 确认安装 → 执行安装 → 健康检查 → 显示最终连接信息。单机端顺序为 Docker/Python → 管理员密钥 → 二次确认 → API/图片公网地址 → 端口 → 线程池容量 → PostgreSQL `DATABASE_URL` → 图片队列 `IMAGE_QUEUE_DATABASE_URL` → 安装目录 → Release → WARP；主控/监控端和生图端会按各自的集群参数继续询问。已有安装目录会保留原存储后端，JSON、SQLite、Git 仅作为旧部署兼容项。

启用 WARP / Privoxy / FlareSolverr：

```bash
curl -fsSL https://raw.githubusercontent.com/biubiubiu125/chatgpt2api/main/deploy/install.sh -o /tmp/chatgpt2api-install.sh
  sudo env NONINTERACTIVE=1 MODE=docker WITH_WARP=1 INSTALL_DIR=/opt/chatgpt2api \
  CHATGPT2API_INSTALL_TARGET=standalone \
  CHATGPT2API_AUTH_KEY='replace-with-a-manual-admin-key' \
  DATABASE_URL='postgresql+psycopg2://user:password@postgres-host:5432/chatgpt2api_app' \
  CHATGPT2API_BASE_URL='https://api.example.com' \
  IMAGE_QUEUE_DATABASE_URL='postgresql+psycopg2://user:password@postgres-host:5432/chatgpt2api_image_queue' \
  bash /tmp/chatgpt2api-install.sh --non-interactive --with-warp
```

常用环境变量：

| 变量 | 说明 |
| --- | --- |
| `MODE` | `docker` 或 `python` |
| `WITH_WARP` | `0` 或 `1` |
| `INSTALL_DIR` | 安装目录，默认 `/opt/chatgpt2api` |
| `CHATGPT2API_PORT` / `PORT` | 宿主机端口，默认 `3000` |
| `CHATGPT2API_AUTH_KEY` / `AUTH_KEY` | 管理员密钥；新安装必须手动填写 |
| `CHATGPT2API_INSTALL_TARGET` | `standalone`、`api-main` 或 `worker` |
| `CHATGPT2API_RELEASE_REF` | 固定 Release 提交；不要使用 `main` 等可变分支 |
| `CHATGPT2API_IMAGE` | 要使用的 GHCR 或本地镜像 |
| `CHATGPT2API_IMAGE_DIGEST` | 自定义 Release 时必须提供的 `sha256` digest |
| `STORAGE_BACKEND` | 新安装默认 `postgres`；旧部署可显式保留 `json`、`sqlite` 或 `git` |
| `DATABASE_URL` | PostgreSQL 账号库地址，必须指向 `chatgpt2api_app` |
| `APP_DATABASE_URL` | 集群应用库；必须指向 `chatgpt2api_app` |
| `IMAGE_QUEUE_DATABASE_URL` | 持久图片队列 PostgreSQL，始终必填 |
| `CHATGPT2API_BASE_URL` | 对外 API/图片基础地址；standalone 公网部署必须设置 |
| `CHATGPT2API_IMAGE_BASE_URL` | Worker 或 standalone 对外图片基础地址 |

帮助和状态：

```bash
sudo bash /tmp/chatgpt2api-install.sh --help
sudo bash /tmp/chatgpt2api-install.sh status --install-dir /opt/chatgpt2api
```

安装完成后检查：

```bash
cd /opt/chatgpt2api
docker compose ps
docker compose logs --tail=200 app
curl -fsS 'http://127.0.0.1:3000/health/live?format=json'
```

脚本不会覆盖已有的 `data/config.json`；如果安装目录已有文件但不是 Git 仓库，会停止以避免误覆盖。

## 方式二：普通 Docker 部署

适合不需要 WARP / FlareSolverr 的场景。标准 Compose 不会自动创建 PostgreSQL，需要自行准备 `chatgpt2api_app` 和 `chatgpt2api_image_queue` 两个 PostgreSQL 数据库；Compose 会先执行数据目录权限和数据库角色初始化任务，再启动应用：

```bash
git clone https://github.com/biubiubiu125/chatgpt2api.git
cd chatgpt2api
mkdir -p data
cp .env.example .env
CHATGPT2API_AUTH_KEY='replace-with-a-manual-admin-key'
printf '{"auth-key":"%s"}\n' "$CHATGPT2API_AUTH_KEY" > data/config.json
```

编辑 `.env`，至少确认：

```dotenv
CHATGPT2API_AUTH_KEY=
CHATGPT2API_IMAGE=ghcr.io/biubiubiu125/chatgpt2api@sha256:c70f118780c9b6e194353b09e8530e20eeed2496cddf9f80ee36c41775178f0a
STORAGE_BACKEND=postgres
DATABASE_URL=postgresql+psycopg2://user:password@postgres-host:5432/chatgpt2api_app
IMAGE_QUEUE_DATABASE_URL=postgresql+psycopg2://user:password@postgres-host:5432/chatgpt2api_image_queue
```

启动、检查和停止：

```bash
docker compose pull
docker compose up -d
docker compose ps
curl -fsS 'http://127.0.0.1:3000/health/live?format=json'
docker compose down
```

访问地址：

```text
Web 面板：http://localhost:3000
API：http://localhost:3000/v1
```

如果 GHCR 包是私有的，先登录：

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
docker compose pull
```

## 方式三：本地 Compose 试跑

该方式自带 PostgreSQL 17，账号存储使用 SQLite，默认访问 `http://localhost:8000`：

```bash
mkdir -p data
CHATGPT2API_AUTH_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf '{"auth-key":"%s"}\n' "$CHATGPT2API_AUTH_KEY" > data/config.json
docker compose -f docker-compose.local.yml up -d --build
docker compose -f docker-compose.local.yml ps
```

只建议用于本机试跑，不要把示例数据库密码直接用于公网环境。

## 方式四：主从集群部署

集群模式由 API 主节点和图片 Worker 从节点组成：

- 主节点运行 API、管理台、账号存储和 PostgreSQL。
- Worker 通过 WireGuard 私网访问 `chatgpt2api_app` 和 `chatgpt2api_image_queue`。
- Worker 公网只暴露 `/images/`、`/image-thumbnails/` 和健康检查。
- Worker 返回图片 URL 后，主节点校验 URL 归属和可达性，再返回给客户端。
- Worker 不提供管理台和普通 API，不需要填写管理员密钥；脚本只生成内部运行密钥。

### 主节点

主节点需要公网 UDP `51820`，WireGuard 私网地址固定为 `10.77.0.1`：

```bash
curl -fsSL https://raw.githubusercontent.com/biubiubiu125/chatgpt2api/main/deploy/install.sh -o /tmp/chatgpt2api-install.sh
sudo env WIREGUARD_SERVER_ENDPOINT=主节点公网IP或域名 \
  CHATGPT2API_AUTH_KEY='replace-with-a-manual-admin-key' \
  POSTGRES_PASSWORD='主节点数据库密码' \
  POSTGRES_ADMIN_PASSWORD='主节点管理密码' \
  bash /tmp/chatgpt2api-install.sh main
sudo bash /tmp/chatgpt2api-install.sh create-worker 1
sudo bash /tmp/chatgpt2api-install.sh status
```

主节点会生成：

```text
/opt/chatgpt2api/join/worker-1.join
/opt/chatgpt2api/join/join-signing.pub
```

将这两个文件安全复制到 Worker 主机的 `/opt/chatgpt2api/join/`。

### Worker 节点

Worker 编号 `1` 对应 WireGuard 地址 `10.77.0.11`。运行后脚本会自动生成公开图片配置，并在激活后用 `worker-check` 验收公开 URL：

```bash
curl -fsSL https://raw.githubusercontent.com/biubiubiu125/chatgpt2api/main/deploy/install.sh -o /tmp/chatgpt2api-install.sh
sudo mkdir -p /opt/chatgpt2api/join
# 将主节点生成的 worker-1.join 和 join-signing.pub 复制到上面的目录
sudo env CHATGPT2API_IMAGE_BASE_URL=https://img-1.example.com/images \
  CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE=proxy \
  bash /tmp/chatgpt2api-install.sh worker /opt/chatgpt2api/join/worker-1.join
sudo bash /tmp/chatgpt2api-install.sh worker-check
```

Worker 图片地址必须是客户端可访问的 `http` 或 `https` 地址，不能指向 localhost、内网、链路本地或保留地址，也不能包含 query / fragment；路径只能是空路径或 `/images`，这样脚本生成的 Worker Nginx 片段才会匹配。`CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE=direct` 时会直接把 Worker 公开在宿主机端口上，`proxy` 时会保持 Worker 只监听 `127.0.0.1`。激活阶段先验收 WireGuard、数据库、心跳和文件写入；加入完成后再由 `worker-check` 验收公开 URL。若反向代理尚未就绪，Worker 会保持运行，修好 Nginx 后重新执行 `worker-check` 即可。Nginx 示例在 `deploy/nginx-worker-images.example.conf`，脚本会生成 `deploy/nginx-worker-images.conf`；其余路径返回 `403`。

集群脚本命令：

- `main`：安装或修复主节点和 PostgreSQL。
- `create-worker 1`：生成一次性 Worker 加入文件并登记 WireGuard peer。
- `worker <join-file>`：校验并消费 join 文件，启动 Worker。
- `rotate-worker 1`：废弃旧的待加入记录并重新生成配置。
- `worker-check`：检查 WireGuard、数据库角色、心跳、文件写入和图片 URL。
- `status`：查看安装目录、Compose、WireGuard 和 Worker 状态。

`worker-*.join`、数据库密码和 `.env` 不要提交到 Git 仓库。

## 方式五：源码运行

适合二次开发、调试和前端开发。源码一键安装需要 Python `3.13`、`uv`、Node.js 和 npm：

一键脚本选择 `MODE=python` 时，会先检查 Python `>=3.13`、构建前端、安装 Sharp 图像放大运行时、执行 `uv sync --frozen` 和 PostgreSQL 队列预检，有 systemd 的 Linux 主机注册并重启
`chatgpt2api.service`；没有 systemd 时使用受管 PID 文件和 `nohup` 作为后备，并统一等待
`/health?format=json&scope=runtime` 通过后才结束安装。

```bash
git clone https://github.com/biubiubiu125/chatgpt2api.git
cd chatgpt2api
uv sync --frozen
cp .env.example .env
mkdir -p data
CHATGPT2API_AUTH_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf '{"auth-key":"%s"}\n' "$CHATGPT2API_AUTH_KEY" > config.json
# 编辑 .env，至少填写 IMAGE_QUEUE_DATABASE_URL；再执行下面两条命令
CHATGPT2API_ENV_FILE="$PWD/.env" uv run --frozen python -m scripts.bootstrap_database_roles
CHATGPT2API_ENV_FILE="$PWD/.env" PORT=8000 uv run --frozen python -m scripts.run_uvicorn
```

前端：

```bash
cd web-vue
npm ci --no-audit --no-fund
npm run dev
```

源码方式读取项目根目录的 `config.json` 和 `data/`。后端默认监听 `0.0.0.0:3000`，本地开发建议显式设置 `PORT=8000`。

## 本地构建和自动构建

本地构建单架构镜像：

```bash
docker buildx build --platform linux/amd64 -t chatgpt2api:local --load .
```

多架构验证：

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t chatgpt2api:test .
```

`.github/workflows/docker-publish.yml` 会在 `main` push、`v*` tag 或手动触发时构建并发布 `linux/amd64`、`linux/arm64`。前端构建阶段固定使用 BuildKit 的构建机架构，避免在 arm64 QEMU 中执行前端依赖安装；`web-vue/package-lock.json` 使用现代 lockfile，Docker 中的 npm 安装关闭 audit / fund 网络请求。Job 超时时间为 45 分钟，便于尽快暴露真正失败步骤。

发布前必须同步更新 `deploy/release-manifest.env` 和 `deploy/install.sh` 里的默认 release pin；工作流会在镜像构建前严格校验两者一致，不再允许源码提交、GHCR 镜像和一键安装脚本指向不同版本。manifest 中的 `UV_VERSION` 同时用于 Docker `build-args` 和 CI 的 `uv sync`。

截图中的 `Error: The operation was canceled` 代表 GitHub Actions Job 被取消，不等于 `npm ci` 本身报错。排查时查看取消前最后一个步骤，并分别验证：

```bash
cd web-vue
npm ci --no-audit --no-fund
npm run build
cd ..
docker buildx build --platform linux/amd64 -t chatgpt2api:local --load .
```

## 反向代理和健康检查

标准 Compose 默认只绑定宿主机 `127.0.0.1:3000`，可以由同机 Nginx 对外提供服务：

```nginx
location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_read_timeout 600s;
}
```

健康检查：

```bash
curl -fsS 'http://127.0.0.1:3000/health/live?format=json'
curl -i 'http://127.0.0.1:3000/health?format=json&scope=runtime'
docker compose ps
docker compose logs --tail=200 app
```

`/health/live` 只判断进程存活；`/health?scope=runtime` 还会检查运行时和图片队列，异常时可能返回 `503`。

## 升级前备份

升级前建议备份 `.env` 和 `data/`：

```bash
cd /opt/chatgpt2api
mkdir -p backups
tar -czf backups/chatgpt2api-$(date +%Y%m%d-%H%M%S).tgz .env data
```

如果没有 `.env`，只备份数据：

```bash
tar -czf backups/chatgpt2api-$(date +%Y%m%d-%H%M%S).tgz data
```

也可以在后台设置页配置 Cloudflare R2 备份。

## 升级：普通 Docker

```bash
cd /opt/chatgpt2api
mkdir -p backups
tar -czf backups/chatgpt2api-$(date +%Y%m%d-%H%M%S).tgz .env data
docker compose pull
docker compose up -d
docker compose ps
curl -fsS 'http://127.0.0.1:3000/health/live?format=json'
```

## 升级：WARP / FlareSolverr

```bash
cd /opt/chatgpt2api
mkdir -p backups
tar -czf backups/chatgpt2api-$(date +%Y%m%d-%H%M%S).tgz .env data
docker compose -f docker-compose.warp.yml pull
docker compose -f docker-compose.warp.yml up -d
docker compose -f docker-compose.warp.yml ps
```

## 升级：源码运行

```bash
cd chatgpt2api
git pull
uv sync --frozen
cd web-vue
npm ci --no-audit --no-fund
npm run build
```

然后按你的进程管理方式重启后端。不要删除 `data/`、`config.json` 或 `.env`。

## 回滚

回滚前先备份当前 `.env` 和 `data/`，再选择旧版本：

```bash
cd /opt/chatgpt2api
git log --oneline -n 20
git checkout <旧版本commit>
docker compose up -d
```

如果使用远程 GHCR 镜像，不要回滚到可变 tag；从旧版本提交的 manifest 读取不可变 digest：

```bash
git show <旧版本commit>:deploy/release-manifest.env | grep '^CHATGPT2API_IMAGE='
export CHATGPT2API_IMAGE="$(git show <旧版本commit>:deploy/release-manifest.env | sed -n 's/^CHATGPT2API_IMAGE=//p')"
docker compose pull
docker compose up -d
```

恢复数据前先停止容器：

```bash
docker compose down
tar -xzf backups/你的备份文件.tgz
docker compose up -d
```

## 常用维护命令

```bash
cd /opt/chatgpt2api
docker compose ps
docker compose logs --tail=200 app
docker compose restart
```

WARP 部署：

```bash
docker compose -f docker-compose.warp.yml ps
docker compose -f docker-compose.warp.yml logs --tail=200 app
docker compose -f docker-compose.warp.yml restart
```

不要直接执行 `rm -rf data`、删除 `.env` 或覆盖 `config.json`，除非已经确认存在可用备份。
