<p align="center">
  <img src="web-vue/public/logo.svg" width="120" alt="ChatGPT2API logo" />
</p>
<h1 align="center">ChatGPT2API</h1>

<p align="center">ChatGPT 官网能力 → OpenAI 兼容 API 网关</p>
<p align="center">
  <img src="https://img.shields.io/badge/License-CNC--1.0-red.svg" />
  <img src="https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/FastAPI-ready-009688?logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/Vue-3-4FC08D?logo=vue.js&logoColor=white" />
  <img src="https://img.shields.io/badge/Vite-7-646CFF?logo=vite&logoColor=white" />
  <img src="https://img.shields.io/badge/Docker-GHCR-2496ED?logo=docker&logoColor=white" />
</p>
<p align="center"><strong>当前代码版本：v2.7.1-rc.1</strong> | <a href="https://github.com/biubiubiu125/chatgpt2api/pkgs/container/chatgpt2api">Docker 镜像</a> | <a href="https://github.com/biubiubiu125/chatgpt2api/releases">全部版本</a></p>
<p align="center"><strong>作者 / 维护者：biubiubiu125</strong></p>

---

## 项目定位

ChatGPT2API 是一个自托管网关，用于把 ChatGPT 官网能力封装成 OpenAI 兼容 API，并提供 Vue 管理控制台。

当前代码重点支持：

- OpenAI 兼容文本、搜索、图片、Responses、Messages 和可编辑文件任务入口。
- 多账号池、账号分组、账号代理、代理组、稳定代理运行时和调用日志。
- GPT-Image-2 持久生图队列：PostgreSQL 存任务，本地或 WebDAV 存图片，Worker 自适应并发，支持重启恢复、幂等和结果确认。
- 主从集群链路：支持 `standalone`、`api-main`、`worker` 三种角色，Worker 通过 WireGuard 私网连接主库和队列库，只公开图片与健康检查路径。
- 注册账号链路：临时邮箱、GPTMail、Outlook Token 邮箱池、Microsoft passwordless 验证、plus alias 和按时段自动补号。
- 管理控制台：账号、用户密钥、日志、实时监控、图片、代理、注册、备份、对话画图和系统设置。
- 普通用户密钥可调用受支持的 API 并使用对话画图；管理员密钥额外拥有完整管理控制台权限。

主要代码分层：

- `main.py`、`api/app.py`：创建 FastAPI 应用、加载配置、按节点角色挂载 API 或 Worker 公共路由，并通过 `python -m scripts.run_uvicorn` 启动。
- `api/ai.py`、`api/image_tasks.py`：OpenAI / Anthropic 兼容接口、持久图片任务、编辑任务和可编辑 PPT / PSD 文件任务入口。
- `api/accounts.py`、`api/system.py`、`api/register.py`、`api/prompts.py`：管理台接口、账号池、存储、图片图库、监控、注册和提示词来源。
- `services/protocol/`：ChatGPT Web 上游协议、SSE（Server-Sent Events，服务端推送）、Responses、Messages、搜索和图片协议适配。
- `services/image_queue/`、`services/image_task_service.py`：PostgreSQL 任务与 Job、幂等、租约、资源控制、恢复、制品交付和 ACK（结果确认）。
- `services/storage/`、`services/image_storage_service.py`：JSON、SQLite、PostgreSQL、Git 账号存储，以及本地、WebDAV、双写和图片索引。
- `services/register/`、`services/register_service.py`：临时邮箱、GPTMail、Outlook Token、Microsoft passwordless、plus alias 和自动补号。
- `services/cluster_*.py`、`services/returned_url_verifier.py`：WireGuard 主从加入、数据库角色、Worker 心跳和公开图片 URL 校验。
- `web-vue/`：Vue 3 + Vite 管理控制台，包含 Dashboard、Accounts、Gallery、Studio、Register、Monitor、Logs、Proxy、Settings、Cluster 和 Debug 页面。
- `scripts/`、`deploy/`、`docker-compose*.yml`、`Dockerfile`：前端构建、图片放大、配置迁移、备份恢复、一键安装、普通 Compose、WARP 和主从集群部署。
- `tests/`：后端单元测试、接口测试、队列测试、注册链路测试和 PostgreSQL 专项测试；`web-vue/scripts/` 保存前端闭环检查脚本。

发布仓库只保留主服务、前端、部署文件、正式测试和长期维护文档；本地配置、运行数据、缓存、临时计划文档不应提交。

---

## 核心能力

- **OpenAI 兼容 API**：`/v1/chat/completions`、`/v1/responses`、`/v1/messages`、`/v1/search`、`/v1/images/generations`、`/v1/images/edits` 等。
- **GPT-Image-2 生图**：外部图片入口使用 `gpt-image-2`，内部负责账号调度、上游协议、下载、校验、放大和保存。
- **PostgreSQL 持久队列**：一个请求对应一个 `image_task`，每张图片对应一个 `image_job`，全部必需 job 成功后任务才成功。
- **可靠交付**：生成、下载、PIL 校验、放大或原图回退、真实宽高读取、原子保存、URL/b64 返回和结果落库完成后才结束任务。
- **幂等防丢图**：图片请求必须携带 `Idempotency-Key`、`X-NewAPI-Request-Id`、`X-OneAPI-Request-Id` 或 `client_task_id`。
- **资源自适应 Worker**：根据 CPU、内存、Swap、线程数、数据库连接池和磁盘压力决定是否继续领取 job。
- **主从集群部署**：API 主节点和 Worker 从节点拆分运行，join token、数据库角色标记、心跳、公开图片 URL 校验和投递状态形成闭环。
- **账号租约并发**：通过 `image_account_leases` 维护单账号图片并发，避免多 Worker 超额占用同一账号。
- **图片存储**：支持本地、WebDAV、双写、索引、标签、缩略图、下载、压缩、清理和 R2 备份。
- **代理与出口**：支持账号代理、账号组代理、多出口代理组、备用出口、WARP、Privoxy、FlareSolverr 和 Cloudflare clearance。
- **自动构建镜像**：推送到 `main` 或创建 `v*` tag 后，GitHub Actions 自动构建 `linux/amd64` 和 `linux/arm64` 镜像并发布到 GHCR。

---

## 功能架构

```mermaid
flowchart TB
  Client["OpenAI 兼容客户端"] --> API["/v1 API"]
  Admin["管理员密钥"] --> Console["Vue 管理控制台"]
  User["普通用户密钥"] --> Studio["对话画图"]

  Console --> AdminAPI["/api 管理接口"]
  Studio --> API
  API --> Auth["身份认证 / 配额"]
  Auth --> Protocol["ChatGPT Web 协议层"]
  Protocol --> Accounts["账号池 / 代理出口"]
  Protocol --> Upstream["ChatGPT 官网"]
  Protocol --> Logs["调用日志 / 实时监控"]

  API --> Queue["PostgreSQL 生图队列"]
  Queue --> Worker["固定 Worker 池"]
  Queue --> Cluster["Worker 心跳 / 加入记录"]
  Cluster --> Worker
  Worker --> Accounts
  Worker --> Artifacts["data/images 或 WebDAV"]
  Worker --> Queue

  AdminAPI --> Storage["账号与密钥存储 JSON / SQLite / PostgreSQL / Git"]
  AdminAPI --> Cluster
  AdminAPI --> Queue
  AdminAPI --> Artifacts
  AdminAPI --> Backup["R2 备份 / 恢复"]
```

> [!WARNING]
> 免责声明：
>
> 本项目涉及对 ChatGPT 官网文本生成、图片生成与图片编辑等相关接口的逆向研究，仅供个人学习、技术研究与非商业性技术交流使用。
>
> - 严禁将本项目用于任何商业用途、盈利性使用、批量操作、自动化滥用或规模化调用。
> - 严禁将本项目用于破坏市场秩序、恶意竞争、套利倒卖、二次售卖相关服务，以及任何违反 OpenAI 服务条款或当地法律法规的行为。
> - 严禁将本项目用于生成、传播或协助生成违法、暴力、色情、未成年人相关内容，或用于诈骗、欺诈、骚扰等非法或不当用途。
> - 使用者应自行承担全部风险，包括账号被限制、临时封禁、永久封禁以及违规使用导致的法律责任。
> - 使用本项目即视为你已充分理解并同意本免责声明全部内容。

---

## 快速开始

### 部署方式选择

| 方式 | 适用场景 | 是否需要本地源码 | 默认端口 |
| :--- | :--- | :---: | :---: |
| 一键脚本 + GHCR | 服务器快速上线，使用已发布镜像 | 否 | `3000` |
| Docker Compose | 需要手动维护 `.env`、镜像和数据 | 否 | `3000` |
| 本地 Compose | 本机完整试跑，附带 PostgreSQL 17 | 是 | `8000` |
| 主从集群脚本 | API 主节点与图片 Worker 分机部署 | 否 | 主节点 `3000` |
| 源码运行 | 二次开发、调试和前端开发 | 是 | 按 `PORT` 设置 |

所有标准 Docker 方式都要求提供可连接且允许应用用户建表的 PostgreSQL 图片队列。账号存储使用 `json` 时，账号数据可以继续保存在 `data/`，但 `IMAGE_QUEUE_DATABASE_URL` 仍不能省略。

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

标准/WARP Compose 的 `database-init` 会在应用启动前创建并校验数据库角色标记；权限不足会阻止应用启动。集群主节点使用 `deploy/postgres-init/001-create-cluster-databases.sh` 自动完成同等的数据库和角色授权。

### 一键安装脚本

一键脚本位于 `deploy/install.sh`，支持交互式安装和真正的非交互式安装。脚本会生成认证密钥，写入 `.env` 与 `data/config.json`，下载对应 Compose 文件，拉取镜像并等待应用健康检查通过。

#### 交互式安装（推荐第一次使用）

服务器需要有 `bash`、`curl`、Docker 和 Docker Compose v2：

```bash
curl -fsSL https://raw.githubusercontent.com/biubiubiu125/chatgpt2api/d887be015b77abfcfc210814a4ed125b8a3cb8b0/deploy/install.sh -o /tmp/chatgpt2api-install.sh
sudo bash /tmp/chatgpt2api-install.sh
```

脚本会依次询问运行模式、端口、线程池容量、安装目录和 Release 引用；Docker 模式要求固定提交 SHA，Python 模式可使用版本标签。随后脚本会询问账号存储后端、图片队列 PostgreSQL 地址和是否启用 WARP。默认安装目录是 `/opt/chatgpt2api`，默认端口是 `3000`。

#### 非交互式一键安装

只需要准备一个可用的 PostgreSQL 图片队列地址：

```bash
curl -fsSL https://raw.githubusercontent.com/biubiubiu125/chatgpt2api/d887be015b77abfcfc210814a4ed125b8a3cb8b0/deploy/install.sh -o /tmp/chatgpt2api-install.sh
  sudo env NONINTERACTIVE=1 MODE=docker INSTALL_DIR=/opt/chatgpt2api PORT=3000 \
  STORAGE_BACKEND=json \
  CHATGPT2API_BASE_URL='https://api.example.com' \
  IMAGE_QUEUE_DATABASE_URL='postgresql+psycopg2://user:password@postgres-host:5432/chatgpt2api_image_queue' \
  bash /tmp/chatgpt2api-install.sh --non-interactive
```

非交互模式下：

- `CHATGPT2API_AUTH_KEY` 留空时自动生成管理员密钥，并写入 `/opt/chatgpt2api/data/config.json` 和 `.env`。
- `CHATGPT2API_IMAGE` 留空时使用 `ghcr.io/biubiubiu125/chatgpt2api@sha256:c70f118780c9b6e194353b09e8530e20eeed2496cddf9f80ee36c41775178f0a`。
- `CHATGPT2API_WARP_IMAGE`、`CHATGPT2API_PRIVOXY_IMAGE` 和 `CHATGPT2API_FLARESOLVERR_IMAGE` 也默认使用固定 digest。
- `CHATGPT2API_RELEASE_REF` 默认固定到当前 Release 提交；不要改成 `main` 等可变分支。Docker 模式的自定义引用必须是 40 位提交 SHA，Python 模式才允许使用版本标签。
- `STORAGE_BACKEND=json` 时不要求账号存储数据库；`postgres` 需要 `DATABASE_URL`，`git` 需要 `GIT_REPO_URL` 和 `GIT_TOKEN`。
- `IMAGE_QUEUE_DATABASE_URL` 始终必填，脚本会在启动前校验。
- standalone 模式必须提供 `CHATGPT2API_BASE_URL` 或 `CHATGPT2API_IMAGE_BASE_URL`；脚本不会再把任意请求 `Host` 当作图片公网地址。
- 可用 `INSTALL_DIR`、`PORT`、`CHATGPT2API_AUTH_KEY`、`CHATGPT2API_IMAGE`、`CHATGPT2API_BASE_URL` 和 `CHATGPT2API_IMAGE_BASE_URL` 覆盖默认值。

启用 WARP / Privoxy / FlareSolverr：

```bash
curl -fsSL https://raw.githubusercontent.com/biubiubiu125/chatgpt2api/d887be015b77abfcfc210814a4ed125b8a3cb8b0/deploy/install.sh -o /tmp/chatgpt2api-install.sh
  sudo env NONINTERACTIVE=1 MODE=docker WITH_WARP=1 INSTALL_DIR=/opt/chatgpt2api \
  CHATGPT2API_BASE_URL='https://api.example.com' \
  IMAGE_QUEUE_DATABASE_URL='postgresql+psycopg2://user:password@postgres-host:5432/chatgpt2api_image_queue' \
  bash /tmp/chatgpt2api-install.sh --non-interactive --with-warp
```

安装完成后检查：

```bash
cd /opt/chatgpt2api
docker compose ps
docker compose logs --tail=200 app
curl -fsS 'http://127.0.0.1:3000/health/live?format=json'
curl -fsS 'http://127.0.0.1:3000/health?format=json&scope=runtime'
```

脚本帮助和已有安装目录状态：

```bash
sudo bash /tmp/chatgpt2api-install.sh --help
sudo bash /tmp/chatgpt2api-install.sh status --install-dir /opt/chatgpt2api
```

脚本的集群子命令是：

- `main`：安装或修复 API 主节点和 PostgreSQL。
- `create-worker 1`：在主节点生成 `join/worker-1.join`，并登记 Worker 的 WireGuard peer。
- `worker /opt/chatgpt2api/join/worker-1.join`：在从节点校验并消费一次性 join 文件，启动 Worker。
- `rotate-worker 1`：废弃旧的待加入记录并重新生成 Worker 配置。
- `worker-check`：检查 Worker 的 WireGuard、数据库角色、心跳、文件写入和公开图片 URL。
- `status`：查看安装目录、Compose 状态、WireGuard 和 Worker 加入状态。

> `main`、`worker` 集群命令会先生成并校验 WireGuard、join 文件和公开图片配置；Worker 激活阶段先验收内部链路，加入完成后由 `worker-check` 验收公开 URL。若反向代理尚未就绪，Worker 会保持运行，修好 Nginx 后重新执行 `worker-check`。单机 `MODE=docker` 才适合上面的完全非交互式命令。

脚本不会覆盖已有的 `data/config.json`；如果安装目录已经存在但不是 Git 仓库，脚本会停止，避免误覆盖其他文件。

### Docker 运行

推荐直接使用 GHCR（GitHub Container Registry）镜像。标准 Compose 不会自动创建 PostgreSQL；它会先执行数据目录权限和数据库角色初始化任务，再启动应用：

```bash
git clone https://github.com/biubiubiu125/chatgpt2api.git
cd chatgpt2api
mkdir -p data
cp .env.example .env
```

生成管理员密钥并写入 Docker 配置：

```bash
CHATGPT2API_AUTH_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf '{"auth-key":"%s"}\n' "$CHATGPT2API_AUTH_KEY" > data/config.json
```

编辑 `.env`，至少确认：

```dotenv
CHATGPT2API_AUTH_KEY=
CHATGPT2API_IMAGE=ghcr.io/biubiubiu125/chatgpt2api@sha256:c70f118780c9b6e194353b09e8530e20eeed2496cddf9f80ee36c41775178f0a
IMAGE_QUEUE_DATABASE_URL=postgresql+psycopg2://user:password@postgres-host:5432/chatgpt2api_image_queue
```

启动和检查：

```bash
docker compose pull
docker compose up -d
docker compose ps
curl -fsS 'http://127.0.0.1:3000/health/live?format=json'
```

默认访问地址：

- Web 面板：`http://localhost:3000`
- API 地址：`http://localhost:3000/v1`
- 存储目录：`./data`
- 运行配置：`./data/config.json`
- 容器内服务端口：`3000`

`CHATGPT2API_AUTH_KEY` 和 `data/config.json` 中的 `auth-key` 二选一即可，环境变量优先。`config.example.yaml` 只用于说明配置结构，示例密钥不能直接用于生产。账号存储的 `DATABASE_URL` 与图片队列的 `IMAGE_QUEUE_DATABASE_URL` 是两个独立配置。

如果 GHCR 包设置为私有，部署机先登录：

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
docker compose pull
```

如果只是在本机完整试跑，可以使用附带 PostgreSQL 的本地 Compose：

```bash
mkdir -p data
CHATGPT2API_AUTH_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf '{"auth-key":"%s"}\n' "$CHATGPT2API_AUTH_KEY" > data/config.json
docker compose -f docker-compose.local.yml up -d --build
```

本地 Compose 默认访问 `http://localhost:8000`，账号存储使用 SQLite，图片队列使用同一 Compose 内的 PostgreSQL 17。该方式只适合本机试跑，不要把示例数据库密码直接用于公网环境。

### 主从集群部署

集群模式把 API 主节点和图片 Worker 从节点拆开部署：

- 主节点运行 API、管理台、账号存储和 PostgreSQL。
- Worker 通过 WireGuard 私网访问两个 PostgreSQL 数据库，只处理图片队列。
- Worker 公网只暴露 `/images/`、`/image-thumbnails/` 和健康检查路径。
- Worker 返回图片 URL 后，主节点会校验 URL 归属和可达性，再返回给客户端。

主节点准备：

```bash
curl -fsSL https://raw.githubusercontent.com/biubiubiu125/chatgpt2api/d887be015b77abfcfc210814a4ed125b8a3cb8b0/deploy/install.sh -o /tmp/chatgpt2api-install.sh
sudo env WIREGUARD_SERVER_ENDPOINT=主节点公网IP或域名 \
  POSTGRES_PASSWORD='主节点数据库密码' \
  POSTGRES_ADMIN_PASSWORD='主节点管理密码' \
  bash /tmp/chatgpt2api-install.sh main
sudo bash /tmp/chatgpt2api-install.sh create-worker 1
sudo bash /tmp/chatgpt2api-install.sh status
```

主节点脚本会创建 `chatgpt2api_app`、`chatgpt2api_image_queue`，并生成：

```text
/opt/chatgpt2api/join/worker-1.join
/opt/chatgpt2api/join/join-signing.pub
```

把这两个文件安全复制到 Worker 主机的 `/opt/chatgpt2api/join/`。Worker 主机准备：

```bash
curl -fsSL https://raw.githubusercontent.com/biubiubiu125/chatgpt2api/d887be015b77abfcfc210814a4ed125b8a3cb8b0/deploy/install.sh -o /tmp/chatgpt2api-install.sh
sudo mkdir -p /opt/chatgpt2api/join
# 将主节点生成的 worker-1.join 和 join-signing.pub 复制到上面的目录
sudo env CHATGPT2API_IMAGE_BASE_URL=https://img-1.example.com/images \
  CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE=proxy \
  bash /tmp/chatgpt2api-install.sh worker /opt/chatgpt2api/join/worker-1.join
sudo bash /tmp/chatgpt2api-install.sh worker-check
```

`CHATGPT2API_IMAGE_BASE_URL` 必须是客户端可访问的 `http` 或 `https` 地址，不能指向 localhost、内网地址、链路本地地址、保留地址，也不能带 query 或 fragment；路径只能是空路径或 `/images`，这样脚本生成的 Worker Nginx 片段才会匹配。`CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE=direct` 时会直接把 Worker 公开在宿主机端口上，`proxy` 时会保持 Worker 只监听 `127.0.0.1`。Worker 激活阶段先验收内部链路，加入完成后由 `worker-check` 验收公开 URL；如果公开反向代理尚未就绪，Worker 会保持运行，修好 Nginx 后重新执行 `worker-check`。Worker 的 Nginx 示例配置在 `deploy/nginx-worker-images.example.conf`，脚本会生成 `deploy/nginx-worker-images.conf`；该配置只放行图片、缩略图和健康检查，其余路径返回 `403`。

集群安装前请确认：主节点的 UDP `51820` 已放行，主节点 WireGuard 私网地址为 `10.77.0.1`，Worker 编号 `1` 对应 `10.77.0.11`，并且两个节点的时间和系统权限正常。不要把 `worker-*.join`、数据库密码或 `.env` 提交到 Git 仓库。

### 反向代理与健康检查

标准 Compose 将应用绑定到宿主机 `127.0.0.1:3000`，适合由同机 Nginx 对外提供服务：

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

存活检查不依赖账号和图片队列：

```bash
curl -fsS 'http://127.0.0.1:3000/health/live?format=json'
```

运行时健康检查会校验图片队列，适合 Docker healthcheck：

```bash
curl -i 'http://127.0.0.1:3000/health?format=json&scope=runtime'
docker compose ps
docker compose logs --tail=200 app
```

`/health?scope=business` 还会检查是否存在可用账号；`/health?scope=runtime` 主要检查运行时和图片队列。健康状态异常时可能返回 `503`，但 `/health/live` 仍可用于判断进程是否存活。

### Docker 镜像自动构建

仓库内置 `.github/workflows/docker-publish.yml`：

- push 到 `main`：构建并推送 `main`、`sha-*` 标签。
- push `v*` tag：构建并推送 tag、semver、`sha-*` 标签。
- `workflow_dispatch`：支持在 GitHub Actions 页面手动触发。
- 构建平台：`linux/amd64`、`linux/arm64`。
- 前端阶段使用构建机架构，避免在 arm64 QEMU（模拟器）中重复执行前端依赖安装。
- Job 有明确超时；如果超过 45 分钟会失败并保留可定位的步骤，而不是等待 GitHub 6 小时后显示 `cancelled`。
- 工作流权限：`contents: read`、`packages: write`。
- `deploy/release-manifest.env` 是生产发布的唯一版本元数据；发布前必须与当前 `GITHUB_SHA` 完全一致，过期 manifest 会在构建前直接失败，避免出现“已发布但一键脚本仍部署旧版本”。
- manifest 中的 `UV_VERSION` 同时驱动 Docker 构建和 CI 后端依赖安装，Python 一键模式也读取同一版本。

镜像地址：

```bash
docker pull ghcr.io/biubiubiu125/chatgpt2api@sha256:c70f118780c9b6e194353b09e8530e20eeed2496cddf9f80ee36c41775178f0a
docker pull ghcr.io/biubiubiu125/chatgpt2api:sha-d887be0
```

`main` 分支标签仅用于开发追踪；生产 Docker 部署使用上面的 digest，并让安装脚本从同一提交 SHA 下载部署文件。

如果 GitHub Actions 报 `denied: permission_denied: write_package`，到 GHCR 包 `chatgpt2api` 的设置中打开仓库的 Actions 访问权限，并授予 `Write`；工作流本身也必须保留 `packages: write`。如果部署机拉取私有包返回 `403`，先登录 GHCR；公开部署则把包可见性设为 `Public`。

截图中出现的 `Error: The operation was canceled` 只表示 Job 被取消，不等于 `npm ci` 返回了依赖错误。当前工作流为构建设置 45 分钟上限；遇到失败时先查看取消前最后一个步骤，再分别运行 `docker buildx build`、`npm ci` 和 `npm run build`，不要只根据红色的最后一行判断根因。

### 本地构建镜像

构建完整多阶段镜像：

```bash
git clone https://github.com/biubiubiu125/chatgpt2api.git
cd chatgpt2api
docker buildx build --platform linux/amd64 -t chatgpt2api:local --load .
```

临时使用本地镜像：

```bash
CHATGPT2API_IMAGE=chatgpt2api:local docker compose up -d
```

如果要验证多架构清单但不推送：

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t chatgpt2api:test .
```

### WARP / FlareSolverr 稳定代理部署

如果注册、登录或图片链路经常遇到 Cloudflare 拦截，可以使用附带的 WARP + Privoxy + FlareSolverr 方案。手动部署：

```bash
mkdir -p data
cp .env.example .env
CHATGPT2API_AUTH_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf '{"auth-key":"%s"}\n' "$CHATGPT2API_AUTH_KEY" > data/config.json
# 编辑 .env，至少填写 IMAGE_QUEUE_DATABASE_URL、CHATGPT2API_WARP_IMAGE、CHATGPT2API_PRIVOXY_IMAGE
cat >> .env <<'EOF'
CHATGPT2API_IMAGE=ghcr.io/biubiubiu125/chatgpt2api@sha256:c70f118780c9b6e194353b09e8530e20eeed2496cddf9f80ee36c41775178f0a
CHATGPT2API_WARP_IMAGE=caomingjun/warp@sha256:da12ba946c7e44665ef25de1fc7d22ef432a9fa8b71fa32dc7790e1b5f27cd7f
CHATGPT2API_PRIVOXY_IMAGE=vimagick/privoxy@sha256:8db03d3e5a36800e2c7e32f17b47e21e18f476bf492f0a50e2fc43073f6bb21f
CHATGPT2API_FLARESOLVERR_IMAGE=flaresolverr/flaresolverr@sha256:139dfee1c6f89249c8d665d1333a42e8ec74ec0a86bc6bb1c8461e10d3a66a47
EOF
docker compose -f docker-compose.warp.yml pull
docker compose -f docker-compose.warp.yml up -d
docker compose -f docker-compose.warp.yml ps
```

该 Compose 会启动：

- `warp-proxy`：提供 WARP SOCKS5 出口，默认宿主机端口 `40000`。
- `privoxy`：把 WARP SOCKS5 转成 HTTP 代理，默认宿主机端口 `40080`。
- `flaresolverr`：默认宿主机端口 `8191`，用于刷新 Cloudflare clearance。
- `init-config`：幂等写入 `proxy_runtime` 默认配置。
- `app`：启动 ChatGPT2API 主服务，默认宿主机端口 `3000`。

代理优先级为：账号个人代理 > 账号组/代理组 > 显式任务代理 > 默认代理 > 稳定代理运行时 > 直连。更详细的 FlareSolverr 配置见 `docs/flaresolverr-cloudflare.md`。

### 本地开发

源码一键安装需要 Python `3.13`、`uv`、Node.js 和 npm：

一键脚本的 `MODE=python` 会构建前端、安装 Sharp 图像放大运行时，再使用 `uv sync --frozen` 安装后端依赖；检测到 systemd 时注册并重启
`chatgpt2api.service`，否则使用带 PID 文件的 `nohup` 后备进程，并等待 `/health?format=json&scope=runtime`。

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
npm ci
npm run dev
```

后端默认监听 `0.0.0.0:3000`；本地开发一般建议显式设置 `PORT=8000`。源码运行时，后端读取项目根目录的 `config.json` 和 `data/`；Docker 方式读取宿主机 `data/config.json`。

常用验证命令：

```bash
uv run pytest -q
uv run pytest -q tests/services/test_image_task_service.py tests/protocol/test_durable_image_protocols.py tests/api/test_image_queue_routes.py tests/services/test_image_service_gallery.py
```

PostgreSQL 队列专项测试：

```bash
export TEST_IMAGE_QUEUE_DATABASE_URL='postgresql+psycopg2://user:password@localhost:5432/chatgpt2api_test'
uv run pytest -q tests/image_queue/test_repository_postgres.py
```

前端构建和维护脚本：

```bash
cd web-vue
npm ci --no-audit --no-fund
npm run build
```

---

## 配置说明

### 账号存储后端

`STORAGE_BACKEND` 控制账号池和认证密钥的存储方式：

| 值 | 说明 |
| :--- | :--- |
| `json` | 本地 JSON 文件，默认使用 `data/accounts.json` 和 `data/auth_keys.json`。 |
| `sqlite` | SQLite 数据库，默认可使用 `data/accounts.db`。 |
| `postgres` | 外部 PostgreSQL，需要配置指向 `chatgpt2api_app` 的 `DATABASE_URL`。 |
| `git` | Git 私有仓库，需要配置 `GIT_REPO_URL` 和 `GIT_TOKEN`。 |

说明：

- 账号存储后端只负责账号池和认证密钥。
- 系统设置、日志、概览统计、图片索引、注册配置和图片队列按各自模块保存。
- 使用 `sqlite` 时，若未设置 `DATABASE_URL`，会自动使用 `data/accounts.db`。
- 使用 `postgres` 时必须设置指向 `chatgpt2api_app` 的 PostgreSQL `DATABASE_URL`。
- 使用 `git` 时还可以通过 `GIT_BRANCH`、`GIT_FILE_PATH` 和 `GIT_AUTH_KEYS_FILE_PATH` 指定分支和文件路径。

### GPT-Image-2 持久生图队列

图片任务固定使用 PostgreSQL，与账号存储的 `DATABASE_URL` 相互独立。生产环境建议显式配置：

```dotenv
IMAGE_QUEUE_DATABASE_URL=postgresql+psycopg2://user:password@host:5432/chatgpt2api_image_queue
```

队列状态：

- `queued`：任务已入队，等待领取。
- `running`：正在执行上游生成或下载。
- `saving`：正在校验、放大或保存最终制品。
- `retrying`：当前阶段失败，等待有限重试。
- `success`：所有必需图片均已完成并落库。
- `failed`：任务明确失败。
- `canceled`：任务已取消。

完整处理链路：

1. API 校验身份、内容、模型、图片输入和幂等键。
2. PostgreSQL 写入 `image_task`，按 `n` 拆分 `image_job`。
3. Worker 使用租约领取 job，并通过账号租约限制单账号并发。
4. 调用 ChatGPT Web 上游，记录 conversation、图片 URL 和阶段 checkpoint。
5. 下载图片并通过 PIL 校验；启用放大时使用 Sharp 或 Pillow，放大失败明确回退原图。
6. 原子保存最终 artifact，读取真实宽高并生成 URL 或 base64 结果。
7. 更新 job、task、日志、配额和事件；客户端断开不会让成功结果丢失。

运行约束：
- `IMAGE_QUEUE_GENERATION_CONCURRENCY` 是显式生成并发上限，未设置时默认取运行时图片并发和 `IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP` 的较小值；默认上限为 `64`。资源控制器会按 CPU、内存、Swap、数据库连接池和磁盘压力动态降速或暂停。
- 外部图片模型使用 `gpt-image-2`，`n` 当前限制为 `1-4`。
- PostgreSQL 暂不可用时，图片接口返回 `503 image_queue_unavailable`；文本接口仍可工作，应用会按 `IMAGE_QUEUE_STARTUP_RETRY_SECONDS` 自动重试连接。
- 队列默认最多等待 `50` 个任务，单个未开始任务默认最多等待 `1800` 秒。
- 旧版 `data/image_tasks.json` 只读导入一次，不再作为任务主存储。
- 过期租约会被回收；旧租约不能覆盖新租约提交的结果。
- 私有输入图、mask、下载中间图不会作为公开图片列表或 `/images/*` 文件直接暴露。

成功条件：

- 上游返回图片。
- 图片下载成功。
- 图片通过 PIL 校验。
- 本地放大成功，或明确回退原图。
- 读取最终真实 `width` / `height`。
- 图片保存成功。
- URL 或 base64 结果生成成功。
- 结果和事件写入 PostgreSQL。

下游断开后，使用相同幂等键再次请求可取回同一任务结果。相同幂等键但请求内容不同会返回冲突，避免误覆盖或重复生图。

交付和保留：

- `/v1/images/*`、图片版 `/v1/chat/completions` 和图片版 `/v1/responses` 返回前会重新读取最终 artifact，确认文件存在、sha 匹配且宽高可读。
- 标准 `/v1` 协议返回结果时会记录“已尝试投递”，响应中会带 `task_id`，便于失败恢复、日志检索和人工排查。
- 原生 `/api/image-tasks/*` 支持显式 `POST /api/image-tasks/{task_id}/ack`；客户端确认结果已保存后再 ACK。
- 未投递过的成功结果持续保护；已尝试投递但未 ACK 的结果至少按 `IMAGE_QUEUE_DELIVERY_GRACE_SECONDS` 保护，之后按终态保留策略清理。
- 逻辑备份的 `backup_required` 与同一套保护规则一致，避免清理和备份语义分裂。

### 主从集群和 Worker 投递

`CHATGPT2API_NODE_ROLE` 决定当前节点职责：

| 角色 | API | Worker | 说明 |
| :--- | :---: | :---: | :--- |
| `standalone` | 是 | 是 | 单机模式，默认值，API 和图片 Worker 同进程运行。 |
| `api-main` | 是 | 否 | 主节点模式，负责 API、管理台、账号、注册、队列提交和结果验证。 |
| `worker` | 否 | 是 | 从节点模式，只领取图片 job，只公开图片文件和健康检查。 |

集群闭环：

1. 主节点使用 `docker-compose.cluster-main.yml` 启动 PostgreSQL 和 API，`deploy/postgres-init/001-create-cluster-databases.sh` 创建 `chatgpt2api_app`、`chatgpt2api_image_queue` 并写入数据库角色标记。
2. `create-worker` 生成签名 join 文件，写入 `chatgpt2api_worker_join_token`，并把 Worker 的 WireGuard peer 加到主节点。
3. Worker 校验 join 文件签名、数据库角色标记和一次性 token，消费成功后写入 `/app/data/worker.joined`，再启动 `docker-compose.cluster-worker.yml`。
4. Worker 写入心跳、有效并发、资源暂停原因和图片域名投递状态；主节点 `/api/cluster/state` 汇总节点、队列、账号、注册和投递健康。
5. 集群模式下图片结果采用 URL-only 投递：Worker 保存最终图片并返回自己的公开 URL，主节点确认 URL 属于该 Worker 的 `CHATGPT2API_IMAGE_BASE_URL` 且可访问后再返回给下游。

Worker 角色必须配置：

```dotenv
CHATGPT2API_NODE_ROLE=worker
CHATGPT2API_WORKER_ID=worker-1
CHATGPT2API_WIREGUARD_IP=10.77.0.11
CHATGPT2API_IMAGE_BASE_URL=https://img-1.example.com/images
CHATGPT2API_CLUSTER_ID=cluster-xxxxxxxxxxxxxxxx
APP_DATABASE_URL=postgresql://chatgpt2api_runtime:password@10.77.0.1:5432/chatgpt2api_app
IMAGE_QUEUE_DATABASE_URL=postgresql://chatgpt2api_runtime:password@10.77.0.1:5432/chatgpt2api_image_queue
```

`CHATGPT2API_IMAGE_BASE_URL` 必须是公网可访问的 `http` 或 `https` 地址，不能包含 query / fragment，不能解析到 localhost、内网、链路本地或保留地址。

### 自动补号

注册服务支持按时段自动补号：

```yaml
threads: 4
auto_schedule_enabled: true

register_peak:
  time_range: "09:00-18:00"
  target_available: 100
  threads: 4

register_offpeak:
  time_range: "18:00-09:00"
  target_available: 30
  threads: 2
```

两个时段必须覆盖全天且不能重叠。自动调度启用时，当前时段的 `target_available` 和 `threads` 生效；关闭自动调度时使用全局 `target_available` 和 `threads`。注册提交也受图片队列资源控制器保护，避免和图片生成抢占 CPU、内存、线程和数据库连接。

### 关键环境变量

| 配置项 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `CHATGPT2API_AUTH_KEY` | 无 | 管理员主密钥；也可在 `config.json` 使用 `auth-key`。 |
| `CHATGPT2API_PORT` | `3000` | Docker Compose 宿主机端口，默认只绑定 `127.0.0.1`。 |
| `CHATGPT2API_RELEASE_REF` | `d887be015b77abfcfc210814a4ed125b8a3cb8b0` | 一键安装使用的固定 Release 提交；Docker 模式必须是 40 位 SHA，Python 模式可使用版本标签。 |
| `CHATGPT2API_IMAGE` | `ghcr.io/biubiubiu125/chatgpt2api@sha256:c70f118780c9b6e194353b09e8530e20eeed2496cddf9f80ee36c41775178f0a` | Compose 使用的镜像。 |
| `CHATGPT2API_IMAGE_DIGEST` | 无 | 仅传入 digest 时自动拼成 `ghcr.io/...@sha256:...`。 |
| `CHATGPT2API_WARP_IMAGE` | `caomingjun/warp@sha256:da12ba946c7e44665ef25de1fc7d22ef432a9fa8b71fa32dc7790e1b5f27cd7f` | WARP 出口镜像；启用 WARP 时默认使用固定 digest。 |
| `CHATGPT2API_PRIVOXY_IMAGE` | `vimagick/privoxy@sha256:8db03d3e5a36800e2c7e32f17b47e21e18f476bf492f0a50e2fc43073f6bb21f` | WARP 代理镜像；启用 WARP 时默认使用固定 digest。 |
| `CHATGPT2API_FLARESOLVERR_IMAGE` | `flaresolverr/flaresolverr@sha256:139dfee1c6f89249c8d665d1333a42e8ec74ec0a86bc6bb1c8461e10d3a66a47` | WARP 运行时的 Cloudflare 清障服务镜像。 |
| `CHATGPT2API_BASE_URL` | 无 | standalone 对外 API/图片基础地址；公网部署必须显式设置，避免信任任意请求 `Host`。 |
| `CHATGPT2API_THREAD_TOKENS` | `80` | AnyIO 后端线程池容量。 |
| `STORAGE_BACKEND` | `json` | 账号池和认证密钥存储后端。 |
| `DATABASE_URL` | 无 | `postgres` 账号存储后端使用；集群主从由 `APP_DATABASE_URL` 承担应用库。 |
| `APP_DATABASE_URL` | 无 | 集群应用库，必须指向 `chatgpt2api_app`。 |
| `CHATGPT2API_NODE_ROLE` | `standalone` | 节点角色：`standalone`、`api-main` 或 `worker`。 |
| `CHATGPT2API_WORKER_ID` | 无 | Worker 稳定编号，例如 `worker-1`。 |
| `CHATGPT2API_WIREGUARD_IP` | 无 | Worker WireGuard 私网 IP，例如 `10.77.0.11`。 |
| `CHATGPT2API_IMAGE_BASE_URL` | 无 | Worker 对外返回的图片基础 URL。 |
| `CHATGPT2API_CLUSTER_ID` | 脚本自动生成 | 集群 ID，主节点和 Worker 必须一致。 |
| `CHATGPT2API_WORKER_JOINED_MARKER_FILE` | `/app/data/worker.joined` | Worker join token 消费和激活标记。 |
| `IMAGE_QUEUE_GENERATION_CONCURRENCY` | 自动计算，默认不超过 `64` | 图片生成 Worker 显式并发上限，最终不超过 `IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP`。 |
| `IMAGE_QUEUE_DATABASE_URL` | 无 | GPT-Image-2 持久队列 PostgreSQL；标准/WARP Compose 缺失时拒绝启动。 |
| `IMAGE_QUEUE_ARTIFACT_ROOT` | `data/images` | 队列图片 artifact 根目录；容器中为 `/app/data/images`。 |
| `IMAGE_QUEUE_MAX_BACKLOG` | `50` | 持久队列最多允许等待的未开始任务数。 |
| `IMAGE_QUEUE_PENDING_TTL_SECONDS` | `1800` | 单个未开始任务允许等待的最长秒数。 |
| `IMAGE_QUEUE_CLAIM_MAX_RUNTIME_SECONDS` | `1800` | 单个 Worker 租约最大运行秒数。 |
| `IMAGE_QUEUE_RECOVERY_ACCOUNT_TIMEOUT_SECONDS` | `900` | 恢复原账号不可用时最多等待的秒数。 |
| `IMAGE_QUEUE_STARTUP_RETRY_SECONDS` | `5` | PostgreSQL 冷启动不可用时的重试间隔。 |
| `IMAGE_QUEUE_PROTOCOL_WAIT_TIMEOUT_SECONDS` | `300` | 标准 OpenAI 协议同步等待图片终态的最长秒数。 |
| `IMAGE_QUEUE_VERIFY_RETURNED_URL` | `true` | 主节点返回集群 Worker 图片 URL 前进行可达性校验。 |
| `IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS` | `5` | 单次公开图片 URL 校验超时秒数。 |
| `IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS` | `3` | 公开图片 URL 校验重试次数。 |
| `IMAGE_QUEUE_DELIVERY_GRACE_SECONDS` | `604800` | 已尝试投递但未 ACK 的结果保护窗口。 |
| `IMAGE_QUEUE_TERMINAL_RETENTION_SECONDS` | `2592000` | 已投递或已 ACK 的终态任务保留时间。 |
| `IMAGE_PROMPT_SUFFIX_ENABLED` | `true` | 是否追加只输出最终图片的默认提示词后缀。 |
| `IMAGE_PROMPT_SUFFIX` | 内置中文后缀 | 覆盖默认图片提示词后缀。 |

WARP / FlareSolverr 初始化还支持 `WARP_SOCKS_PORT`、`PRIVOXY_PORT`、`FLARESOLVERR_PORT`、`FLARESOLVERR_LOG_LEVEL`、`CHATGPT2API_PROXY_RUNTIME_*` 和 `CHATGPT2API_FLARESOLVERR_URL`。完整配置见 `.env.example` 和 `config.example.yaml`。

---

## 功能详情

### API 兼容能力

> 兼容的是本项目已实现的 ChatGPT Web 逆向场景，不等同于官方 OpenAI 全量 API 代理。

- `GET /v1/models`：返回当前可暴露模型列表。
- `POST /v1/chat/completions`：支持文本、搜索和图片场景，支持 `stream`、工具、搜索选项和推理强度字段。
- `POST /v1/responses`：支持文本、搜索和 `image_generation` 工具。
- `POST /v1/messages`：Anthropic Messages 兼容入口，同时支持 `Authorization` 和 `x-api-key`。
- `POST /v1/search`：ChatGPT 搜索兼容入口。
- `POST /v1/images/generations`：OpenAI Images 文生图兼容入口，支持 `n=1..4`。
- `POST /v1/images/edits`：OpenAI Images 图生图/编辑兼容入口，支持 multipart、远程 URL、base64、data URL 和多参考图。
- `GET/POST /api/image-tasks/*`：原生异步图片任务接口，提交只入队，查询任务状态，成功后可 ACK。
- `GET /api/cluster/state`：管理员查看节点角色、Worker 心跳、队列、账号、注册和图片 URL 投递健康。
- `POST /v1/editable-file-tasks`、`GET /v1/editable-file-tasks`：统一 PPT / PSD 可编辑文件任务。
- `POST /v1/ppt/generations`、`POST /v1/psd/generations`：PPT / PSD 快捷入口。
- `GET /files/{file_path}`：文件任务产物下载，需要任务所属身份的 API Key。
- `/v1` 兼容错误响应统一为 OpenAI 风格 `{"error":{"message","type","param","code"}}`；图片队列错误会保留 `image_queue_unavailable`、`idempotency_key_required`、`idempotency_conflict` 等机器可读错误码。
- 远程图片 URL 会做 SSRF 防护，拒绝 localhost、内网、链路本地、保留地址、带凭据 URL 和不可解析域名。

### 对话画图工作台

- 支持文本对话、搜索模式、文生图、图生图、多图参考和 Markdown 渲染。
- 生图任务以内联状态展示排队、运行、保存、成功和失败。
- 支持搜索引用、图片结果、会话历史、推理强度和上游占位内容解析。
- 支持局部重绘 mask 画布、参考图预览压缩和移动端历史侧栏。
- 可配置图片成功后删除上游官网 conversation，默认关闭，便于恢复和排查。
- 普通用户登录后只能进入对话画图；管理员登录后可以进入 Dashboard、Accounts、Settings、Proxy、Register、Logs、Monitor、Docs、Gallery 和 Debug 等管理页面。

### 图片链路和诊断

- 请求头和任务事件记录 `task_id`、`job_id`、`call_id`、NewAPI 请求头、账号、conversation id、图片 URL 和关键阶段。
- 网络超时、临时 429、上游 5xx、下载失败、保存失败会按阶段有限重试。
- 上游无图、策略拒绝和不可恢复账号问题会明确失败。
- URL 返回前校验文件存在、非空、sha 匹配且真实宽高可读。
- 本地放大失败时返回原图并记录 fallback 事件。
- 监控区分 `queued`、`running`、`saving`、`retrying`、`success`、`failed`、`canceled`，并展示队列、Worker、资源和调用事件。

### 账号、导入和注册

- 账号管理支持搜索、筛选、分页、批量刷新、导出、编辑、分组、代理设置和异常账号处理。
- 支持本地 CPA JSON、远程 CPA、Sub2API 和 access token 导入。
- Sub2API / CPA 支持远程分组读取、选择、去重和批量导入。
- 管理员可以创建、启用、禁用和删除普通用户密钥；密钥只保存哈希，创建时明文仅返回一次。
- 注册链路支持临时邮箱、GPTMail、Outlook Token 邮箱池、Microsoft passwordless 验证和 plus alias。
- 自动补号按时段运行，并受资源控制器和运行时租约保护。

### 代理、存储和运维

- 代理优先级：账号个人代理 > 账号组/代理组 > 显式任务代理 > 默认代理 > 稳定代理运行时 > 直连。
- 代理组支持多出口节点、节点图片并发、轮换间隔、健康展示和测试。
- 支持 WARP、Privoxy、FlareSolverr 和 Cloudflare clearance 测试。
- 支持本地图片存储、WebDAV、双写、图片索引、标签、下载、压缩和清理。
- R2 备份可覆盖配置、账号、日志、图片索引、概览统计、生图队列逻辑导出和图片文件。
- 设置页支持 API 文档、第三方画布入口、备份、图片存储测试和清理预览。
- 集群管理页展示节点角色、Worker 在线数、队列深度、账号配额、注册状态、图片 URL 和投递健康。
- 多实例部署需要共享 PostgreSQL 和共享图片存储，并按部署拓扑配置会话保持。

---

## API

所有 AI 接口和原生图片任务接口都需要身份认证：

```http
Authorization: Bearer <auth-key>
```

`/v1/messages` 也可以使用：

```http
x-api-key: <user-or-admin-key>
```

图片请求还需要以下任意一个幂等标识：

```http
Idempotency-Key: <stable-request-id>
```

也可以使用 `X-NewAPI-Request-Id`、`X-OneAPI-Request-Id` 或请求体里的 `client_task_id`。如果同一幂等键对应的请求内容发生变化，接口返回冲突而不会重复生图。

OpenAI 兼容入口的错误响应结构：

```json
{
  "error": {
    "message": "image requests require Idempotency-Key",
    "type": "invalid_request_error",
    "param": null,
    "code": "idempotency_key_required"
  }
}
```

图片响应里的 `task_id` 是持久任务 ID。标准 `/v1` 协议返回结果时会自动记录“已尝试投递”；原生 `/api/image-tasks/*` 客户端建议在确认结果已保存后调用 ACK。

### 接口速览

| 接口 | 方法 | 说明 |
| :--- | :--- | :--- |
| `/v1/models` | `GET` | 返回当前模型列表。 |
| `/v1/chat/completions` | `POST` | Chat Completions 兼容入口，支持文本、搜索和图片。 |
| `/v1/responses` | `POST` | Responses 兼容入口，支持文本、搜索和图片工具。 |
| `/v1/messages` | `POST` | Messages 兼容入口。 |
| `/v1/search` | `POST` | ChatGPT 搜索兼容入口。 |
| `/v1/images/generations` | `POST` | OpenAI Images 文生图兼容入口。 |
| `/v1/images/edits` | `POST` | OpenAI Images 图生图/编辑兼容入口。 |
| `/api/image-tasks/generations` | `POST` | 原生异步文生图任务，只入队。 |
| `/api/image-tasks/edits` | `POST` | 原生异步图生图任务，只入队。 |
| `/api/image-tasks` | `GET` | 批量查询图片任务，支持按 ids 过滤。 |
| `/api/image-tasks/quota` | `GET` | 查询账号配额和队列资源可用性。 |
| `/api/image-tasks/{task_id}` | `GET` | 查询图片任务。 |
| `/api/image-tasks/{task_id}/resume-poll` | `POST` | 对可恢复任务追加轮询等待。 |
| `/api/image-tasks/{task_id}/cancel` | `POST` | 取消排队或运行中的图片任务。 |
| `/api/image-tasks/{task_id}/ack` | `POST` | 下游确认已取走结果。 |
| `/api/cluster/state` | `GET` | 集群节点、Worker、队列、账号、注册和投递健康，仅管理员。 |
| `/v1/editable-file-tasks` | `GET/POST` | PPT / PSD / 可编辑文件任务创建与查询。 |
| `/files/{file_path}` | `GET` | 文件任务产物下载，需要任务所属身份的 API Key。 |
| `/api/accounts`、`/api/account-groups` | `GET/POST/DELETE` | 账号池、分组和批量导入管理，仅管理员。 |
| `/api/auth/users` | `GET/POST/DELETE` | 普通用户密钥管理，仅管理员。 |
| `/api/proxy/*` | `GET/POST/DELETE` | 代理配置、代理组、稳定代理运行时和 clearance 测试，仅管理员。 |
| `/api/register/*` | `GET/POST` | 注册任务、GPTMail、Outlook 邮箱池和实时事件，仅管理员。 |
| `/api/images/*` | `GET/POST/DELETE` | 图片列表、标签、缩略图、下载、压缩和清理，仅管理员。 |
| `/api/backups/*` | `GET/POST` | R2 备份、备份详情、下载和删除，仅管理员。 |
| `/api/settings` | `GET/POST` | 系统设置、API 文档、备份和图片存储配置。 |
| `/api/third-party-apps` | `GET` | 第三方画布入口配置。 |
| `/health/live` | `GET` | 进程存活检查，不依赖账号和队列。 |
| `/health` | `GET` | 业务或运行时健康检查。 |

<details>
<summary><code>GET /v1/models</code></summary>
<br>

```bash
curl http://localhost:3000/v1/models \
  -H "Authorization: Bearer <auth-key>"
```

</details>

<details>
<summary><code>POST /v1/chat/completions</code></summary>
<br>

文本 / 搜索示例：

```bash
curl http://localhost:3000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <auth-key>" \
  -d '{
    "model": "gpt-5",
    "messages": [
      {"role": "user", "content": "联网搜索并总结今天的 AI 新闻"}
    ],
    "tools": [{"type": "web_search_preview"}],
    "reasoning_effort": "medium"
  }'
```

聊天生图示例：

```bash
curl http://localhost:3000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <auth-key>" \
  -H "Idempotency-Key: image-chat-001" \
  -d '{
    "model": "gpt-image-2",
    "messages": [
      {"role": "user", "content": "生成一张雨夜东京街头的赛博朋克猫"}
    ],
    "n": 1
  }'
```

</details>

<details>
<summary><code>POST /v1/responses</code></summary>
<br>

```bash
curl http://localhost:3000/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <auth-key>" \
  -H "Idempotency-Key: image-response-001" \
  -d '{
    "model": "gpt-5",
    "input": "生成一张未来感城市天际线图片",
    "tools": [{"type": "image_generation"}]
  }'
```

支持 `image_generation`、`web_search`、`web_search_preview` 和 `web_search_preview_2025_03_11`。

</details>

<details>
<summary><code>POST /v1/images/generations</code></summary>
<br>

```bash
curl http://localhost:3000/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <auth-key>" \
  -H "Idempotency-Key: image-generation-001" \
  -d '{
    "model": "gpt-image-2",
    "prompt": "一只漂浮在太空里的猫",
    "n": 1,
    "response_format": "b64_json"
  }'
```

| 字段 | 说明 |
| :--- | :--- |
| `model` | 图片入口使用 `gpt-image-2`。 |
| `prompt` | 图片生成提示词。 |
| `n` | 生成数量，当前限制 `1-4`。 |
| `size` | 可传尺寸字段，实际效果取决于上游。 |
| `quality` | 图片质量，默认 `auto`。 |
| `response_format` | 支持 `b64_json` 或 `url`。 |
| `stream` | 设为 `true` 时返回图片流式事件。 |
| `client_task_id` | 可作为请求体里的幂等键，也会进入持久任务记录。 |
| `history_disabled` | 默认 `true`，用于生成链路的会话隔离。 |

</details>

<details>
<summary><code>POST /v1/images/edits</code></summary>
<br>

multipart 示例：

```bash
curl http://localhost:3000/v1/images/edits \
  -H "Authorization: Bearer <auth-key>" \
  -H "Idempotency-Key: image-edit-001" \
  -F "model=gpt-image-2" \
  -F "prompt=把这张图改成赛博朋克夜景风格" \
  -F "n=1" \
  -F "image=@./input.png"
```

JSON 图片 URL 示例：

```bash
curl http://localhost:3000/v1/images/edits \
  -H "Authorization: Bearer <auth-key>" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: image-edit-002" \
  -d '{
    "model": "gpt-image-2",
    "prompt": "把这张图改成赛博朋克夜景风格",
    "images": [
      {"image_url": "https://example.com/input.png"}
    ],
    "n": 1
  }'
```

支持字段：`model`、`prompt`、`n`、`size`、`quality`、`response_format`、`stream`、`image`、`images`、`image_url`、`mask`、`client_task_id`。

图片输入支持 multipart 上传、`image_url` / `images` 远程 URL、data URL 和 base64；单个图片最大 50MB，总输入最大 50MB，最多 16 个图片或 mask 引用。`file_id` 图片引用不支持，需改用 `image_url`。

</details>

<details>
<summary><code>POST /api/image-tasks/generations</code></summary>
<br>

原生异步任务接口只入队，不等待生成完成，适合前端工作台、长轮询或业务系统自行恢复结果：

```bash
curl http://localhost:3000/api/image-tasks/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <auth-key>" \
  -d '{
    "client_task_id": "client-image-task-001",
    "model": "gpt-image-2",
    "prompt": "一张高清产品海报",
    "n": 2
  }'
```

查询任务：

```bash
curl http://localhost:3000/api/image-tasks/<task_id> \
  -H "Authorization: Bearer <auth-key>"
```

批量查询任务：

```bash
curl "http://localhost:3000/api/image-tasks?ids=<task_id>,<client_task_id>&limit=100&offset=0" \
  -H "Authorization: Bearer <auth-key>"
```

取消任务：

```bash
curl -X POST http://localhost:3000/api/image-tasks/<task_id>/cancel \
  -H "Authorization: Bearer <auth-key>"
```

恢复轮询：

```bash
curl -X POST http://localhost:3000/api/image-tasks/<task_id>/resume-poll \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <auth-key>" \
  -d '{"extra_timeout_secs": 30}'
```

确认结果已取走：

```bash
curl -X POST http://localhost:3000/api/image-tasks/<task_id>/ack \
  -H "Authorization: Bearer <auth-key>"
```

返回体包含 `status`、`stage`、`delivery_status`、`data`、`error_code`、`created_at`、`updated_at` 等字段；`data` 中的公开 URL 对应最终 artifact，读取前会校验文件、sha 和宽高。

</details>

<details>
<summary><code>POST /v1/messages</code></summary>
<br>

```bash
curl http://localhost:3000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: <user-or-admin-key>" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "gpt-5",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "写一段产品介绍"}
    ]
  }'
```

</details>

<details>
<summary><code>POST /v1/editable-file-tasks</code></summary>
<br>

```bash
curl http://localhost:3000/v1/editable-file-tasks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <auth-key>" \
  -d '{
    "kind": "ppt",
    "prompt": "做一份产品发布会 PPT"
  }'
```

查询任务：

```bash
curl "http://localhost:3000/v1/editable-file-tasks?task_id=<task_id>" \
  -H "Authorization: Bearer <auth-key>"
```

下载任务产物：

```bash
curl "http://localhost:3000/files/<file_path>" \
  -H "Authorization: Bearer <auth-key>" \
  -OJ
```

</details>

---

## 备份和恢复

R2 备份可包含配置、账号、认证密钥快照、日志、概览统计、图片索引、生图队列逻辑导出和 `data/images` 图片文件。备份可使用 Cloudflare R2，并支持轮换、可选 OpenSSL 加密和恢复前校验。

恢复图片队列时建议先停止应用，并确保目标 `IMAGE_QUEUE_DATABASE_URL` 指向空数据库：

```bash
export IMAGE_QUEUE_DATABASE_URL='postgresql+psycopg2://user:password@host:5432/chatgpt2api_image_queue'
python scripts/restore_image_queue_backup.py ./backup-20260728T000000Z.tar.gz
```

恢复脚本会校验图片路径、大小、sha 和备份清单；旧 Worker 心跳和账号租约不会恢复。真实备份密钥、R2 凭据、WebDAV 密码和 Git Token 只能通过运行时配置提供。

---

## 效果展示

<table width="100%">
  <tr>
    <td width="50%"><img src="docs/images/1.png" alt="screenshot 1" border="0"></td>
    <td width="50%"><img src="docs/images/2.png" alt="screenshot 2" border="0"></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/images/3.png" alt="screenshot 3" border="0"></td>
    <td width="50%"><img src="docs/images/4.png" alt="screenshot 4" border="0"></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/images/5.png" alt="screenshot 5" border="0"></td>
    <td width="50%"><img src="docs/images/6.png" alt="screenshot 6" border="0"></td>
  </tr>
</table>

---

## 项目归属

- 作者 / 维护者：[`biubiubiu125`](https://github.com/biubiubiu125)
- 仓库：[`biubiubiu125/chatgpt2api`](https://github.com/biubiubiu125/chatgpt2api)

## Star History

[![Star History Chart](https://api.star-history.com/chart?repos=biubiubiu125/chatgpt2api&type=date&legend=top-left)](https://www.star-history.com/?repos=biubiubiu125%2Fchatgpt2api&type=date&legend=top-left)
