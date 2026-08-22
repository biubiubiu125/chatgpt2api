# GPT-Image-2 PostgreSQL 持久生图队列与自适应可靠交付方案

## 1. 目标与范围

本方案将 chatgpt2api 的全部外部生图入口统一切换为 PostgreSQL 持久任务引擎，解决高并发时无限创建线程、进程重启丢任务、保存失败误报成功，以及下游连接中断后无法找回图片的问题。

部署约束如下：

- 每台服务器只运行一个 chatgpt2api 应用容器，Worker 与 API 在同一进程内管理，不拆分独立 Worker 服务。
- PostgreSQL 使用本机独立实例或外部数据库；图片文件使用持久卷或现有远程存储，不写入 PostgreSQL `bytea`。
- 每台服务器默认拥有独立的 PostgreSQL 和图片存储。如果多个服务器位于同一个负载均衡后方，则必须使用请求粘性，或者改成共享 PostgreSQL 与共享对象存储，才能跨服务器恢复同一幂等请求。
- 不引入 Redis、Celery 或其他消息队列。
- 对外生图模型只允许 `gpt-image-2`；内部继续沿用现有账号选择、Codex/Web 路由、Conversation 解析、编辑和图片下载能力。
- 文本请求保持现有执行链，不进入图片队列。

本次改造按一次发布完成，不保留 JSON 与 PostgreSQL 双写，不允许数据库故障时静默回退旧线程模式。

## 2. 总体架构

全部外部生图入口先通过统一的 `ImageTaskService` 持久化请求，再由进程内 `ImageWorkerManager` 领取单图 Job 执行。上游响应、Conversation 标识、远程图片 URL、下载文件、放大文件和最终 Artifact 均形成可恢复检查点。

```text
外部生图接口
    -> 请求校验、幂等判断、输入图持久化
    -> PostgreSQL image_task + image_jobs
    -> ImageWorkerManager
       -> 生图执行池
       -> 下载/保存池
       -> 放大池
    -> 持久 Artifact
    -> 协议适配器
    -> 下游响应或按任务重新取回
```

`/api/image-tasks/*` 入队后立即返回。`/v1/images/*`、生图模式的 `/v1/chat/completions` 和 `/v1/responses` 也创建同样的持久任务，但当前 HTTP 请求同步等待数据库结果。客户端断开只终止等待，不取消后台任务。

## 3. 数据模型

### 3.1 image_tasks

请求级主表，至少包含：

- `id UUID PRIMARY KEY`
- `owner_key VARCHAR`：调用方或 API Key 的稳定脱敏标识
- `client_task_id VARCHAR`：兼容现有异步任务调用方生成的任务标识
- `idempotency_key VARCHAR`
- `request_hash CHAR(64)`
- `task_type VARCHAR`：`generation` 或 `edit`
- `public_model VARCHAR`：固定为 `gpt-image-2`
- `original_prompt TEXT`
- `effective_prompt TEXT`
- `prompt_suffix_version VARCHAR`
- `request_payload JSONB`：不包含图片 Base64 和认证信息
- `required_jobs INTEGER`
- `succeeded_jobs INTEGER`
- `failed_jobs INTEGER`
- `status VARCHAR`
- `wait_reason VARCHAR`
- `error_code VARCHAR`
- `error_message TEXT`
- `delivery_status VARCHAR`
- `response_attempted_at TIMESTAMPTZ`
- `delivery_acked_at TIMESTAMPTZ`
- `created_at`、`queued_at`、`started_at`、`completed_at`、`updated_at`
- `version BIGINT`

唯一约束为 `(owner_key, idempotency_key)` 和 `(owner_key, client_task_id)`。相同 Key 与相同请求哈希返回原任务；相同 Key 与不同请求哈希返回 HTTP 409。接口统一返回数据库 `task_id`，同时保留 `client_task_id`；现有任务查询兼容使用任一标识。

### 3.2 image_jobs

每张图片对应一个 Job：

- `id UUID PRIMARY KEY`
- `task_id UUID REFERENCES image_tasks(id)`
- `ordinal INTEGER`
- `status VARCHAR`
- `stage VARCHAR`
- `generate_attempts`、`download_attempts`、`save_attempts INTEGER`
- `available_at`、`next_retry_at TIMESTAMPTZ`
- `lease_owner VARCHAR`
- `lease_token UUID`
- `lease_version BIGINT`
- `lease_expires_at`、`heartbeat_at TIMESTAMPTZ`
- `account_id UUID`
- `conversation_id VARCHAR`
- `image_urls JSONB`
- `file_ids JSONB`
- `sediment_ids JSONB`
- `error_code VARCHAR`
- `error_message TEXT`
- 各阶段开始和结束时间

唯一约束为 `(task_id, ordinal)`。每个 Job 固定生成一张图片，原请求的 `n` 不再传递到内部并发池。

### 3.3 image_task_events

追加式审计表，记录 Task/Job/Attempt、状态变化、上游调用、下载、放大、保存、恢复和投递事件。事件上下文只保存脱敏字段；禁止保存 Authorization、Access Token、Cookie 和完整上传内容。

### 3.4 image_task_artifacts

记录输入图、上游原图、放大图和最终图：

- `id UUID PRIMARY KEY`
- `task_id`、`job_id UUID`
- `kind VARCHAR`：`input`、`original`、`upscaled`、`final`
- `status VARCHAR`：`staging` 或 `ready`
- `storage_backend VARCHAR`
- `relative_path TEXT`
- `sha256 CHAR(64)`
- `mime_type VARCHAR`
- `byte_size BIGINT`
- `width`、`height INTEGER`
- `source_url TEXT`
- `created_at`、`ready_at TIMESTAMPTZ`

`relative_path` 唯一，最终 Artifact 使用 `(job_id, kind, sha256)` 唯一约束。

### 3.5 image_account_leases

账号并发槽位表：

- `account_id UUID`
- `slot_no INTEGER`
- `job_id UUID UNIQUE`
- `lease_owner VARCHAR`
- `lease_token UUID`
- `lease_version BIGINT`
- `expires_at`、`heartbeat_at TIMESTAMPTZ`

主键为 `(account_id, slot_no)`。现有账号数据必须获得持久稳定的 `account_id`，不能用会刷新的 Access Token 作为租约主键。

### 3.6 image_worker_state

保存 Worker 实例心跳、当前有效并发、资源采样值、暂停原因和最近恢复时间，为监控和异常退出恢复提供依据。

## 4. 状态机与成功条件

对外 Task 状态为：

- `queued`
- `running`
- `saving`
- `retrying`
- `success`
- `failed`
- `canceled`

内部 Job 阶段为：

- `queued`
- `leased`
- `generating`
- `resolving`
- `downloading`
- `transforming`
- `saving`
- `retry_wait`
- `success`
- `failed`
- `canceled`

Task 状态由子 Job 推导。只有全部必需 Job 成功时 Task 才能成功；任一 Job 最终失败时 Task 失败，但已成功的 Artifact 必须保留，重新执行任务时只重新排队失败 Job。

单个 Job 只有同时满足下列条件才能成功：

1. 上游返回图片引用或图片数据。
2. 图片下载完成。
3. Pillow 严格校验通过。
4. 本地放大完成，或者明确记录回退原图。
5. 重新读取最终文件的真实宽高。
6. 最终文件原子保存成功。
7. 可访问 URL 生成成功。
8. Artifact 和结果记录事务提交成功。

上游只返回文本、无图、明确拒绝或永久性输入错误时直接失败。

## 5. Job 领取、租约与账号并发

调度器使用 PostgreSQL `FOR UPDATE SKIP LOCKED` 在短事务中领取 Job。候选账号仍由现有账号服务筛选，但账号槽位必须在同一个短事务内通过 `image_account_leases` 原子占用。没有账号槽位时 Job 保持排队，`wait_reason=account_capacity`，不能创建线程等待条件变量。

默认租约参数：

- 心跳间隔 15 秒。
- 租约有效期 90 秒。
- 每次领取生成新的 `lease_token` 并递增 `lease_version`。
- 所有 Job 更新均校验 `job_id + lease_token + lease_version`。

租约超时后恢复器可以重新领取。旧 Worker 即使稍后返回，也会因 fencing 校验失败而无法覆盖新结果。

PostgreSQL 事务不能与外部上游调用形成原子事务，因此本方案不承诺上游绝对只调用一次。承诺范围是任务与结果不丢、最终副作用幂等、陈旧 Worker 不覆盖有效结果；进程在极小窗口崩溃时，上游可能收到一次重复调用。

## 6. Worker 与资源自适应

应用进程内仅运行一个 `ImageWorkerManager`，管理四类执行资源：

- 上游生图执行池。
- 图片解析、下载和存储 I/O 池。
- 高 CPU、高内存的放大池。
- 最低优先级的账号注册池。

不再按 Task 或 `n` 动态创建线程。资源控制器周期采集容器 CPU、可用内存、Swap 换入换出、线程数、文件句柄、数据库连接池占用、磁盘剩余空间、账号空闲槽位和近期上游 429/5xx 比例。

控制规则：

- CPU 连续达到 95% 时停止领取新的生成 Job。
- CPU 连续回落到 85% 后逐步恢复。
- 可用内存不足、持续 Swap、数据库连接池超过 85%、磁盘空间不足或上游错误率过高时暂停拉新。
- 恢复采用逐步加一，出现压力时快速减半，避免并发震荡。
- 2000 仅作为不可突破的异常保护值，不代表创建 2000 个线程；实际并发始终受内部线程安全上限、账号槽位和资源门控共同限制。
- 调度优先级为恢复保存、正常生图、定时重试、账号注册。

容器资源判断必须读取 cgroup 限额；不能只读取宿主机总资源。

## 7. 检查点、恢复与重试

上游一旦获得 `conversation_id`、`image_urls`、`file_ids` 或 `sediment_ids`，必须立即写入 Job 检查点，不能等最终 Task 成功。上游 Conversation 的删除或 ACK 必须延后到最终 Artifact 与结果事务提交之后。

重启后按以下顺序恢复：

1. 最终文件存在且严格校验通过：补齐数据库结果并完成 Job。
2. 已存在下载或放大 Artifact：从对应阶段继续保存。
3. 已有 Conversation 或远程 URL：重新解析或下载。
4. 没有可恢复检查点：在生成重试预算内重新调用上游。
5. 重试耗尽：Job 失败并保留完整事件。

默认重试预算：

- 上游生成最多 3 次。
- 图片下载最多 5 次。
- 文件保存最多 5 次。
- 使用指数退避和随机抖动。

网络超时、429、上游 5xx、临时下载失败、临时保存失败和数据库瞬时错误可以重试。已有图片 URL 时优先重下，不重新生成。生成、下载和保存次数分别统计，不复用现有请求级 `retryable` 标记。

## 8. Artifact 幂等保存

最终路径固定为：

```text
task_id/job_id/sha256.png
```

最终图片统一转码为 PNG，再对 PNG 字节计算 SHA-256，避免把 JPEG 或 WebP 仅改扩展名。保存流程为同目录临时文件、flush、fsync、原子替换、非空检查、`PIL.Image.verify()`、重新打开并完整 `load()`、读取真实宽高。

放大失败时必须记录 `upscale_fallback` 并保存原图，尺寸取原图实际值。只有最终文件可读、Artifact 为 `ready` 且结果事务已提交后，才可以生成和返回 URL。

编辑接口的上传、Base64 和远程输入图必须在入队响应前持久化为 input Artifact。所有输入来源统一执行大小限制、SSRF 防护和严格图片校验。

## 9. 幂等请求与可靠交付

幂等键按以下顺序读取：

1. `Idempotency-Key`
2. 稳定的 `X-NewAPI-Request-Id`
3. `/api/image-tasks/*` 已有的 `client_task_id`

没有稳定幂等键时服务端仍会持久化任务，但下游断线后无法确定性关联原请求。NewAPI 调用链必须传递稳定请求标识。

Task 的 `success` 只表示图片已可靠生成和保存，不表示 HTTP 客户端已经收到响应。投递状态独立记录为 `pending`、`response_attempted`、`acknowledged`。提供按 Task ID 和幂等键查询结果的能力，并保留可选 ACK 接口。未确认投递的成功 Artifact 默认不自动清理；磁盘压力时停止接收新生图任务并告警，不删除未确认结果。

## 10. 外部协议与模型约束

外部生图入口只接受 `gpt-image-2`，未指定时默认该模型；其他生图模型返回 HTTP 400。`/v1/models` 不再暴露内部 Codex 图片模型，但文本模型列表和内部执行映射保持不变。

协议适配器统一读取协议无关的最终 Job 结果：

- `/v1/images/*` 的每个 data 项增加真实 `width` 和 `height`。
- `/v1/responses` 图片结果项透传真实尺寸。
- `/v1/chat/completions` 保持现有内容兼容，并增加结构化 `image_results` 扩展字段。
- Stream 只在最终 Artifact 就绪后发送真实尺寸。

## 11. 提示词增强

入队时默认追加以下后缀一次：

```text
请直接生成最终图片，只输出图片结果，不要回复解释、拒绝说明、文字描述或 Markdown。高清画质，细节丰富，主体清晰，构图完整。
```

Task 同时保存 `original_prompt`、`effective_prompt` 和 `prompt_suffix_version`。所有重试只使用已经计算好的 `effective_prompt`，生成、编辑、Chat 和 Responses 使用同一规则。

## 12. 自动补号

按 `Asia/Shanghai` 判断跨午夜时间段：

```yaml
register_peak:
  time_range: "18:00-02:00"
  target_available: 100
  threads: 4

register_offpeak:
  time_range: "02:00-18:00"
  target_available: 30
  threads: 2
```

`target_available` 定义为健康且确认可用于生图的账号数，不是当前空闲账号数。低于目标才注册，高于目标不删除账号。CPU、内存、Swap、数据库或生图队列出现压力时注册暂停，注册任务优先级始终低于图片恢复、保存和生成。

## 13. 监控与安全

实时监控改为从 PostgreSQL 聚合，展示 Task/Job 各状态数量、最老排队时间、平均和 P90 等待时间、当前有效并发、资源暂停原因、账号租约利用率、重试次数、各处理阶段失败数、成功但未确认投递数量、Worker 心跳和过期租约。

原 `completed` 改为“结束窗口”，并拆分成功、失败和取消；界面明确它表示最近保留窗口，不是累计或当日总量。

全链路记录 `task_id`、`job_id`、`call_id`、允许列表内的 NewAPI 请求头、稳定 `account_id`、`conversation_id` 和图片 URL。账号令牌不落事件表，错误信息和请求头必须脱敏。

## 14. 启动迁移与故障行为

发布启动时执行版本化 PostgreSQL 迁移，并使用 PostgreSQL advisory lock 防止重复执行。现有 `image_tasks.json` 可一次性导入终态历史记录后保留为只读旧数据；旧格式没有完整输入和上游检查点的未完成任务不能伪装成可恢复任务，应以 `legacy_interrupted` 原因终止并保留原始文件。

迁移完成后立即关闭 JSON 读写。PostgreSQL 不可用时，图片任务提交返回明确 HTTP 503，不能回退到旧线程执行；文本接口保持可用。备份逻辑改为数据库备份与 Artifact 清单，不再把 `image_tasks.json` 当作活动任务源。

## 15. 代码边界

新增 `services/image_queue/`：

- `models.py`：数据库模型和状态常量。
- `repository.py`：入队、领取、租约、事件和完成事务。
- `scheduler.py`：Job 调度和优先级。
- `worker.py`：单图 Job 执行与阶段切换。
- `resource_controller.py`：容器资源采样和并发门控。
- `recovery.py`：租约回收和检查点恢复。
- `retry_policy.py`：分阶段重试分类与退避。
- `artifact_service.py`：输入和最终 Artifact 原子保存。
- `idempotency.py`：请求规范化、哈希和幂等键处理。

主要修改现有模块：

- `services/image_task_service.py` 改为 PostgreSQL 服务门面，移除 JSON 主存储和每任务守护线程。
- `services/protocol/conversation.py` 抽出单图执行和检查点回调，移除按 `n` 创建线程池。
- `services/account_service.py` 将图片并发改为稳定账号 ID 与数据库租约。
- `services/image_storage_service.py` 和 `services/image_upscale_service.py` 接入 Artifact 与独立资源池。
- `services/register_service.py` 增加峰谷目标和资源保护。
- `services/realtime_monitor_service.py` 改为 PostgreSQL 聚合。
- `services/backup_service.py` 改为数据库与 Artifact 备份语义。
- `api/ai.py`、`api/image_inputs.py` 及 Chat/Responses 协议适配器统一接入持久任务服务。

## 16. 验收标准

实现必须通过以下自动化与故障注入验证：

1. 1000 个排队任务不会产生对应数量的线程，线程数保持在内部安全上限内。
2. `n > 1` 精确拆分 Job，只有全部必需 Job 成功时 Task 才成功。
3. 相同幂等键和请求只创建一个 Task；不同请求复用同一键返回 409。
4. 进程分别在上游返回后、下载后、原子写文件后、数据库提交后崩溃，重启均可按检查点恢复。
5. 租约过期可以重新领取，旧租约不能提交结果。
6. 账号全部繁忙时 Job 留在队列且不占用 Worker 线程。
7. 最终 URL 对应文件始终存在、非空、Pillow 可读，宽高等于实际最终文件。
8. 放大失败时返回原图及原图真实尺寸，并留下明确事件。
9. 下游在成功前断开后，后台任务继续完成；使用相同幂等键能够取得原结果。
10. 429、5xx、下载失败和保存失败按各自预算重试；文本无图直接失败。
11. 外部图片模型只暴露和接受 `gpt-image-2`，内部执行路由不受影响。
12. 峰谷补号跨午夜计算正确，资源压力下不会与生图争抢执行资源。
13. 监控状态、结束窗口和未确认投递统计来自 PostgreSQL，且敏感字段不落库。
