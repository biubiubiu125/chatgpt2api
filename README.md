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
  <img src="https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white" />
</p>
<p align="center"><strong>当前稳定版本：v2.4.5</strong> | <a href="https://github.com/yukkcat/chatgpt2api/releases/tag/v2.4.5">发布说明</a> | <a href="https://github.com/yukkcat/chatgpt2api/releases">全部版本</a></p>

---

## 联系我们

点击链接加入群聊【gemini/gpt-2API 交流群】：

- [https://qm.qq.com/q/yegwCqJisS](https://qm.qq.com/q/yegwCqJisS)

---

## 项目定位

本仓库基于原版 [basketikun/chatgpt2api](https://github.com/basketikun/chatgpt2api) 整理维护，核心仍是把 ChatGPT 官网能力封装为 OpenAI 兼容 API。

本版本使用新的 Vue 控制台，主题和交互与原版前端不同；除前端实现差异外，接口、配置和部署口径会尽量保持与原版一致。

发布仓库只保留主服务、Vue 控制台和必要部署文件；旧版前端、测试文件、临时文档和运行产物不进入发布内容。

---

## 核心能力

- OpenAI 兼容接口，可对接常见 OpenAI SDK、上游网关或客户端。
- ChatGPT 官网图片链路，覆盖图片生成、图片编辑、多图组图编辑和图片任务追踪。
- Vue 管理控制台，包含概览中心、账号管理、日志管理、图片管理、代理管理、注册账号、图像创作、调试中心和系统设置。
- 多账号调度，支持账号导入、刷新、重新登录、额度读取、异常账号处理和批量管理。
- 注册账号链路，支持临时邮箱 / Outlook Token 邮箱读取、验证码等待、注册进度和实时日志。
- 配置管理，覆盖用户密钥、WebDAV、AI 审核、R2 备份、CPA / Sub2API 连接和运行参数。
- 自托管部署，支持 Docker、WARP / Privoxy / FlareSolverr 稳定代理、JSON / SQLite / PostgreSQL / Git 存储后端。

---

## 功能架构

```mermaid
flowchart TB
  Admin["管理员"] --> Console["Vue 管理控制台"]
  User["普通用户"] --> ImageStudio["图像创作"]
  Client["OpenAI 兼容客户端"] --> Gateway["ChatGPT2API 网关"]

  subgraph ConsoleModules["控制台模块"]
    Dashboard["概览中心"]
    Accounts["账号管理"]
    Logs["日志管理"]
    Gallery["图片管理"]
    Proxy["代理管理"]
    Register["注册账号"]
    Settings["系统设置"]
    Debug["调试中心"]
  end

  Console --> ConsoleModules
  ImageStudio --> Gateway
  Gateway --> Runtime["ChatGPT 官网链路"]
  ConsoleModules --> AdminAPI["管理接口"]
  AdminAPI --> Domain["账号池 / 配置 / 日志 / 图片任务"]
  Runtime --> Domain
  Domain --> Storage["data 目录 / SQLite / PostgreSQL / Git"]
```

---

> [!WARNING]
> 免责声明：
>
> 本项目涉及对 ChatGPT 官网文本生成、图片生成与图片编辑等相关接口的逆向研究，仅供个人学习、技术研究与非商业性技术交流使用。
>
> - 严禁将本项目用于任何商业用途、盈利性使用、批量操作、自动化滥用或规模化调用。
> - 严禁将本项目用于破坏市场秩序、恶意竞争、套利倒卖、二次售卖相关服务，以及任何违反 OpenAI 服务条款或当地法律法规的行为。
> - 严禁将本项目用于生成、传播或协助生成违法、暴力、色情、未成年人相关内容，或用于诈骗、欺诈、骚扰等非法或不当用途。
> - 使用者应自行承担全部风险，包括但不限于账号被限制、临时封禁或永久封禁以及因违规使用等所导致的法律责任。
> - 使用本项目即视为你已充分理解并同意本免责声明全部内容；如因滥用、违规或违法使用造成任何后果，均由使用者自行承担。
> - 本项目基于对 ChatGPT 官网相关能力的逆向研究实现，存在账号受限、临时封禁或永久封禁的风险。请勿使用你自己的重要账号、常用账号或高价值账号进行测试。

## 快速开始

### 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/yukkcat/chatgpt2api/main/deploy/install.sh | sudo bash
```

固定安装当前稳定版：

```bash
curl -fsSL https://raw.githubusercontent.com/yukkcat/chatgpt2api/v2.4.5/deploy/install.sh | sudo bash -s -- --branch v2.4.5
```

### Docker 运行

```bash
git clone https://github.com/yukkcat/chatgpt2api.git
cd chatgpt2api
cp .env.example .env
printf '{ "auth-key": "your_secret_key_here" }\n' > config.json
docker compose up -d
```

启动前请先在 `.env` 中设置 `CHATGPT2API_AUTH_KEY`，也可以继续在 `config.json` 中填写 `auth-key`。
仓库只保留 `config.example.yaml` 作为配置示例，运行时真实配置文件仍是本地 `config.json`，不要把本地配置提交到仓库。

- Web 面板：`http://localhost:3000`
- API 地址：`http://localhost:3000/v1`
- 数据目录：`./data`

### WARP / FlareSolverr 稳定代理部署

如果注册或图片链路经常遇到 Cloudflare 拦截，可以启用附带的 WARP + Privoxy + FlareSolverr 方案：

```bash
cp .env.example .env
printf '{ "auth-key": "your_secret_key_here" }\n' > config.json
docker compose -f docker-compose.warp.yml up -d
```

该 compose 会启动：

- `warp-proxy`：提供 WARP SOCKS5 出口。
- `privoxy`：把 WARP SOCKS5 转成 HTTP 代理。
- `flaresolverr`：刷新 Cloudflare clearance。
- `init-config`：幂等写入 `proxy_runtime` 默认配置。
- `app`：启动 ChatGPT2API 主服务。

默认只让上游 OpenAI / ChatGPT 请求走稳定代理，账号邮箱、CPA 等辅助链路不会被强制接管。账号自身配置的代理优先级最高，其次是稳定代理运行时，再其次是显式代理和旧版全局代理。

可在 `.env` 中调整端口和代理运行时参数，也可在后台设置页的「稳定代理运行时」面板手动保存、测试代理和测试 clearance。

### 本地开发

启动后端：

```bash
git clone https://github.com/yukkcat/chatgpt2api.git
cd chatgpt2api
uv sync
uv run main.py
```

启动前端：

```bash
cd chatgpt2api/web-vue
npm install
npm run dev
```

后续更新新版本：

```bash
git pull
docker compose up -d
```

### 账号存储后端配置

支持通过环境变量 `STORAGE_BACKEND` 切换账号池和管理 Key 的存储方式：

- `json` - 本地 JSON 文件（默认）
- `sqlite` - 本地 SQLite 数据库
- `postgres` - 外部 PostgreSQL（需配置 `DATABASE_URL`）
- `git` - Git 私有仓库（需配置 `GIT_REPO_URL` 和 `GIT_TOKEN`）

说明：该配置只影响账号池和管理 Key。系统设置、调用日志、概览统计、图片索引、注册机配置仍按各自模块独立保存，其中概览统计默认写入 `data/dashboard_metrics.json` 并滚动保留最近 30 天。

示例：使用 PostgreSQL

```yaml
environment:
  - STORAGE_BACKEND=postgres
  - DATABASE_URL=postgresql://user:password@host:5432/dbname
```

## 功能详情

### API 兼容能力

- 兼容 `POST /v1/images/generations` 图片生成接口
- 兼容 `POST /v1/images/edits` 图片编辑接口
- 兼容面向图片场景的 `POST /v1/chat/completions`
- 兼容面向图片场景的 `POST /v1/responses`
- `GET /v1/models` 返回 `gpt-image-2`、`codex-gpt-image-2`、`auto`、`gpt-5`、`gpt-5-1`、`gpt-5-2`、`gpt-5-3`、`gpt-5-3-mini`、
  `gpt-5-5`、`gpt-5-mini`
- 支持通过 `n` 返回多张生成结果
- 支持生成可编辑 PPT 文件
- 支持生成可编辑 PSD 文件
- 支持 Codex 中的画图接口逆向，仅 `Plus` / `Team` / `Pro` 订阅可用，模型别名为 `codex-gpt-image-2`，如有需要可自行在其他场景映射回
  `gpt-image-2`，用于和官网画图区分；也就意味着同一账号会同时有官网和 Codex 两份生图额度

### 在线画图功能

- 内置在线画图工作台，支持生成、图片编辑与多图组图编辑
- 支持 `gpt-image-2`、`codex-gpt-image-2`、`auto`、`gpt-5`、`gpt-5-1`、`gpt-5-2`、`gpt-5-3`、`gpt-5-3-mini`、`gpt-5-5`、`gpt-5-mini` 模型选择
- 编辑模式支持参考图上传
- 前端支持多图生成交互
- 本地保存图片会话历史，支持回看、删除和清空
- 支持服务端缓存图片URL
- 图片生成进度追踪，超时后可继续等待
- 图片懒加载与滚动位置记忆，优化大量图片场景性能

### 号池管理功能

- 自动刷新账号邮箱、类型、额度和恢复时间（异步进度追踪）
- 轮询可用账号执行图片生成与图片编辑
- 遇到 Token 失效类错误时自动剔除无效 Token
- 定时检查限流账号并自动刷新
- 支持密码重新登录恢复异常账号，刷新后可自动重登
- 支持网页端配置全局 HTTP / HTTPS / SOCKS5 / SOCKS5H 代理
- 支持 WARP / FlareSolverr 稳定代理运行时
- 支持搜索、筛选、批量刷新、导出、手动编辑和清理账号
- 支持四种导入方式：本地 CPA JSON 文件导入、远程 CPA 服务器导入、`sub2api` 服务器导入、`access_token` 导入
- 支持在设置页配置 `sub2api` 服务器，筛选并批量导入其中的 OpenAI OAuth 账号

### 状态说明

- 发布变更以 [CHANGELOG.md](./CHANGELOG.md) 为准。
- 本地开发过程中的临时文档、测试记录和运行产物不作为发布仓库内容。

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

## API

所有 AI 接口都需要请求头：

```http
Authorization: Bearer <auth-key>
```

<details>
<summary><code>GET /v1/models</code></summary>
<br>

返回当前暴露的图片模型列表。

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer <auth-key>"
```

<details>
<summary>说明</summary>
<br>

| 字段     | 说明                                                                                                               |
| :------- | :----------------------------------------------------------------------------------------------------------------- |
| 返回模型 | `gpt-image-2`、`codex-gpt-image-2`、`auto`、`gpt-5`、`gpt-5-1`、`gpt-5-2`、`gpt-5-3`、`gpt-5-3-mini`、`gpt-5-5`、`gpt-5-mini` |
| 接入场景 | 可接入 Cherry Studio、New API 等上游或客户端                                                                       |

<br>
</details>
</details>

<details>
<summary><code>POST /v1/images/generations</code></summary>
<br>

OpenAI 兼容图片生成接口，用于文生图。

```bash
curl http://localhost:8000/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <auth-key>" \
  -d '{
    "model": "gpt-image-2",
    "prompt": "一只漂浮在太空里的猫",
    "n": 1,
    "response_format": "b64_json"
  }'
```

<details>
<summary>字段说明</summary>
<br>

| 字段              | 说明                                                                     |
| :---------------- | :----------------------------------------------------------------------- |
| `model`           | 图片模型，当前可用值以 `/v1/models` 返回结果为准，推荐使用 `gpt-image-2` |
| `prompt`          | 图片生成提示词                                                           |
| `n`               | 生成数量，当前后端限制为 `1-4`                                           |
| `response_format` | 当前请求模型中包含该字段，默认值为 `b64_json`                            |

<br>
</details>
</details>

<details>
<summary><code>POST /v1/images/edits</code></summary>
<br>

OpenAI 兼容图片编辑接口，可上传图片文件，也可按官方 JSON 格式传入图片链接并生成编辑结果。

```bash
curl http://localhost:8000/v1/images/edits \
  -H "Authorization: Bearer <auth-key>" \
  -F "model=gpt-image-2" \
  -F "prompt=把这张图改成赛博朋克夜景风格" \
  -F "n=1" \
  -F "image=@./input.png"
```

也可以直接传图片 URL：

```bash
curl http://localhost:8000/v1/images/edits \
  -H "Authorization: Bearer <auth-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image-2",
    "prompt": "把这张图改成赛博朋克夜景风格",
    "images": [
      {"image_url": "https://example.com/input.png"}
    ]
  }'
```

<details>
<summary>字段说明</summary>
<br>

| 字段        | 说明                                                   |
| :---------- | :----------------------------------------------------- |
| `model`     | 图片模型， `gpt-image-2`                               |
| `prompt`    | 图片编辑提示词                                         |
| `n`         | 生成数量，当前后端限制为 `1-4`                         |
| `image`     | 需要编辑的图片文件，使用 multipart/form-data 上传      |
| `images`    | JSON 图片引用数组，支持 `{"image_url": "https://..."}` |
| `image_url` | 表单模式下也可直接传图片链接，支持重复字段传多张图     |

<br>
</details>
</details>

<details>
<summary><code>POST /v1/chat/completions</code></summary>
<br>

面向文本、网页搜索与图片场景的 Chat Completions 兼容接口，不是完整通用聊天代理。

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <auth-key>" \
  -d '{
    "model": "gpt-image-2",
    "messages": [
      {
        "role": "user",
        "content": "生成一张雨夜东京街头的赛博朋克猫"
      }
    ],
    "n": 1
  }'
```

<details>
<summary>字段说明</summary>
<br>

| 字段                 | 说明                                                                               |
| :------------------- | :--------------------------------------------------------------------------------- |
| `model`              | 文本、搜索或图片模型；搜索模型会触发网页搜索兼容逻辑                               |
| `messages`           | 消息数组，支持文本、搜索和图片请求内容                                             |
| `n`                  | 图片生成数量，按当前实现解析为图片数量                                             |
| `stream`             | 文本、搜索和图片场景均支持，仍在测试                                               |
| `tools`              | 文本场景支持 `web_search` / `web_search_preview` / `web_search_preview_2025_03_11` |
| `web_search_options` | 传入时会触发网页搜索兼容逻辑                                                       |

<br>
</details>
</details>

<details>
<summary><code>POST /v1/responses</code></summary>
<br>

面向文本、网页搜索和图片生成工具调用的 Responses API 兼容接口，不是完整通用 Responses API 代理。

```bash
curl http://localhost:8000/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <auth-key>" \
  -d '{
    "model": "gpt-5",
    "input": "生成一张未来感城市天际线图片",
    "tools": [
      {
        "type": "image_generation"
      }
    ]
  }'
```

<details>
<summary>字段说明</summary>
<br>

| 字段     | 说明                                                                                         |
| :------- | :------------------------------------------------------------------------------------------- |
| `model`  | 响应中会回显该模型字段，搜索和图片生成会走对应兼容逻辑                                       |
| `input`  | 输入内容；搜索使用最后一条用户文本，图片生成需能解析出提示词                                 |
| `tools`  | 支持 `image_generation`、`web_search`、`web_search_preview`、`web_search_preview_2025_03_11` |
| `stream` | 已实现，但仍在测试                                                                           |

<br>
</details>
</details>

## 社区支持

学 AI , 上 L 站：[LinuxDO](https://linux.do)

## 原版项目贡献者

<a href="https://github.com/basketikun/chatgpt2api/graphs/contributors">
  <img alt="Contributors" src="https://contrib.rocks/image?repo=basketikun/chatgpt2api" />
</a>

## Star History

[![Star History Chart](https://api.star-history.com/chart?repos=yukkcat/chatgpt2api&type=date&legend=top-left)](https://www.star-history.com/?repos=yukkcat%2Fchatgpt2api&type=date&legend=top-left)
