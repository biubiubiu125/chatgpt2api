# GPT-Image-2 PostgreSQL 持久生图队列实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将所有外部生图入口一次性切换到 PostgreSQL 持久任务引擎，在单应用容器内通过资源自适应 Worker、账号租约、检查点恢复和幂等 Artifact 保证高并发下任务与图片结果不丢失。

**Architecture:** API 只负责校验、幂等入队和等待/查询；PostgreSQL 是 Task、Job、事件、Artifact、账号租约与 Worker 状态的唯一事实源。进程内 Worker Manager 用短事务领取单图 Job，继续复用现有上游 Conversation 能力，但由队列负责账号槽位、阶段重试、最终保存、恢复和下游结果复用。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy 2、PostgreSQL、psycopg2、Pillow、psutil、pytest、Vue 3、TypeScript。

## Global Constraints

- 每台服务器只运行一个 chatgpt2api 应用容器；不增加 Redis、Celery 或独立 Worker 容器。
- 图片队列只接受 PostgreSQL；不得回退到 JSON 或 SQLite。SQLite 只允许由测试显式注入。
- 外部生图模型只允许 `gpt-image-2`，内部 Codex/Web 路由与文本请求行为保持兼容。
- 所有外部生图入口统一持久化；客户端断开不能取消后台任务。
- `n` 必须拆成一图一个 Job；全部必需 Job 成功后 Task 才能成功。
- `image_account_concurrency` 继续表示单账号并发数；账号繁忙时 Job 留在队列，不占用等待线程。
- CPU 95% 暂停领取，85% 恢复；2000 仅为内部异常保护值，不允许实际创建 2000 个线程。
- 最终图统一转为 PNG，并按 `task_id/job_id/sha256.png` 幂等保存。
- 图片成功与下游投递分离；未确认投递的成功图片不得自动删除。
- 所有生产代码变更先写失败测试并确认失败原因，再实现最小代码使其通过。

## File Structure

新增 `services/image_queue/`，职责固定如下：

- `types.py`：状态枚举、DTO、检查点和资源快照类型。
- `settings.py`：环境配置、PostgreSQL URL 校验和内部安全常量。
- `models.py`：SQLAlchemy 表模型。
- `database.py`：Engine、Session、版本化迁移和 PostgreSQL advisory lock。
- `idempotency.py`：幂等键、请求规范化、哈希和提示词后缀。
- `repository.py`：Task/Job/事件/租约/结果事务。
- `artifact_service.py`：输入图和最终图严格校验、PNG 转码与原子保存。
- `retry_policy.py`：分阶段错误分类、预算和退避。
- `resource_controller.py`：cgroup 感知资源采样与 AIMD 门控。
- `worker.py`：单图 Job 执行和 Worker Manager 生命周期。
- `recovery.py`：过期租约回收、检查点恢复和旧 JSON 导入。

测试集中在 `tests/image_queue/`，协议和 API 集成测试分别放在 `tests/protocol/` 与 `tests/api/`。

---

### Task 1: PostgreSQL 运行时、配置、模型与版本化迁移

**Files:**
- Modify: `pyproject.toml:7-23`
- Modify: `uv.lock`
- Create: `services/image_queue/__init__.py`
- Create: `services/image_queue/types.py`
- Create: `services/image_queue/settings.py`
- Create: `services/image_queue/models.py`
- Create: `services/image_queue/database.py`
- Create: `tests/image_queue/conftest.py`
- Create: `tests/image_queue/test_settings.py`
- Create: `tests/image_queue/test_database.py`

**Interfaces:**
- Produces: `ImageQueueSettings.from_env() -> ImageQueueSettings`
- Produces: `ImageQueueDatabase(settings, engine=None, allow_non_postgres=False)`
- Produces: `ImageQueueDatabase.start()`, `session()`, `dispose()`
- Produces: Task/Job/Event/Artifact/AccountLease/WorkerState ORM 模型和状态枚举。

- [ ] **Step 1: 写配置和数据库约束的失败测试**

```python
def test_production_queue_rejects_sqlite(monkeypatch):
    monkeypatch.setenv("IMAGE_QUEUE_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    with pytest.raises(ImageQueueConfigurationError, match="PostgreSQL"):
        ImageQueueSettings.from_env()


def test_schema_contains_task_and_job_uniqueness(sqlite_queue_database):
    task_constraints = {tuple(item["column_names"]) for item in inspect(sqlite_queue_database.engine).get_unique_constraints("image_tasks")}
    job_constraints = {tuple(item["column_names"]) for item in inspect(sqlite_queue_database.engine).get_unique_constraints("image_jobs")}
    assert ("owner_key", "idempotency_key") in task_constraints
    assert ("owner_key", "client_task_id") in task_constraints
    assert ("task_id", "ordinal") in job_constraints
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `uv run pytest tests/image_queue/test_settings.py tests/image_queue/test_database.py -v`

Expected: collection 失败，提示 `services.image_queue` 不存在。

- [ ] **Step 3: 添加依赖、类型、设置、ORM 模型和迁移运行器**

`pyproject.toml` 增加：

```toml
"psutil>=7.0.0",
```

开发依赖增加：

```toml
"pytest>=8.4.0",
```

设置对象必须使用以下默认值：

```python
@dataclass(frozen=True)
class ImageQueueSettings:
    database_url: str
    lease_seconds: int = 90
    heartbeat_seconds: int = 15
    poll_interval_seconds: float = 0.5
    result_wait_poll_seconds: float = 0.25
    generation_attempts: int = 3
    download_attempts: int = 5
    save_attempts: int = 5
    cpu_pause_percent: float = 95.0
    cpu_resume_percent: float = 85.0
    absolute_guard: int = 2000
    prompt_suffix_enabled: bool = True
    prompt_suffix: str = "请直接生成最终图片，只输出图片结果，不要回复解释、拒绝说明、文字描述或 Markdown。高清画质，细节丰富，主体清晰，构图完整。"
```

数据库 URL 优先读取 `IMAGE_QUEUE_DATABASE_URL`，其次只接受以 `postgresql` 开头的 `DATABASE_URL`。生产缺少 URL 时队列状态为 unavailable，图片接口返回 503；显式传入 `allow_non_postgres=True` 时测试可使用 SQLite。

迁移运行器创建 `image_queue_schema_migrations`，使用固定 advisory lock 键 `chatgpt2api-image-queue-v1`，按整数版本执行迁移。第一版创建设计文档定义的六张业务表及索引。Engine 使用 `pool_pre_ping=True`、`pool_size=20`、`max_overflow=10`，Session 使用 `expire_on_commit=False`。

- [ ] **Step 4: 更新 lockfile 并运行测试**

Run: `uv lock`

Run: `uv run pytest tests/image_queue/test_settings.py tests/image_queue/test_database.py -v`

Expected: 全部通过。

- [ ] **Step 5: 提交数据库基础设施**

```powershell
git add pyproject.toml uv.lock services/image_queue tests/image_queue/test_settings.py tests/image_queue/test_database.py
git commit -m "feat(image): add postgres queue schema"
```

### Task 2: 幂等键、请求哈希、提示词和外部模型策略

**Files:**
- Create: `services/image_queue/idempotency.py`
- Create: `tests/image_queue/test_idempotency.py`
- Modify: `utils/helper.py:21-135`
- Modify: `services/protocol/openai_v1_models.py:1-85`
- Modify: `services/model_catalog_service.py:18-130`
- Create: `tests/protocol/test_image_model_policy.py`

**Interfaces:**
- Produces: `select_idempotency_key(headers, client_task_id) -> str`
- Produces: `canonical_request_hash(payload) -> str`
- Produces: `build_effective_prompt(prompt, settings) -> tuple[str, str | None]`
- Produces: `require_public_image_model(model) -> str`

- [ ] **Step 1: 写幂等和模型限制的失败测试**

```python
def test_newapi_request_id_is_used_when_idempotency_header_missing():
    headers = {"x-newapi-request-id": " newapi-42 "}
    assert select_idempotency_key(headers, "") == "newapi-42"


def test_request_hash_ignores_trace_and_base_url_but_not_prompt():
    first = canonical_request_hash({"prompt": "cat", "base_url": "https://a", "_call_id": "1"})
    second = canonical_request_hash({"prompt": "cat", "base_url": "https://b", "_call_id": "2"})
    third = canonical_request_hash({"prompt": "dog", "base_url": "https://a", "_call_id": "1"})
    assert first == second
    assert first != third


def test_prompt_suffix_is_appended_once(queue_settings):
    effective, version = build_effective_prompt("画一只猫", queue_settings)
    repeated, repeated_version = build_effective_prompt(effective, queue_settings)
    assert effective.endswith(queue_settings.prompt_suffix)
    assert repeated == effective
    assert version == repeated_version == "v1"


def test_external_image_model_rejects_codex_alias():
    with pytest.raises(ValueError, match="gpt-image-2"):
        require_public_image_model("codex-gpt-image-2")
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/image_queue/test_idempotency.py tests/protocol/test_image_model_policy.py -v`

Expected: 新函数不存在，模型列表仍包含内部图片模型。

- [ ] **Step 3: 实现规范化和外部模型边界**

幂等优先级固定为：

```python
for name in ("idempotency-key", "x-newapi-request-id", "x-oneapi-request-id"):
    value = clean_header(headers, name)
    if value:
        return value[:200]
return clean_client_task_id(client_task_id)
```

请求哈希使用排序、紧凑 UTF-8 JSON，递归排除 `base_url`、`_call_id`、`_trace_image_perf`、`_image_task_context` 和所有认证字段。外部图片模型常量只包含 `gpt-image-2`；内部 `IMAGE_MODELS` 与 `split_image_model()` 保留原能力。

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/image_queue/test_idempotency.py tests/protocol/test_image_model_policy.py -v`

Expected: 全部通过。

- [ ] **Step 5: 提交幂等与模型策略**

```powershell
git add services/image_queue/idempotency.py utils/helper.py services/protocol/openai_v1_models.py services/model_catalog_service.py tests/image_queue/test_idempotency.py tests/protocol/test_image_model_policy.py
git commit -m "feat(image): enforce durable request identity"
```

### Task 3: 队列 Repository、状态聚合和 fencing 租约

**Files:**
- Create: `services/image_queue/repository.py`
- Create: `tests/image_queue/test_repository.py`
- Create: `tests/image_queue/test_repository_postgres.py`
- Create: `tests/fixtures/docker-compose.postgres.yml`

**Interfaces:**
- Produces: `enqueue_task(request: EnqueueRequest) -> EnqueueResult`
- Produces: `get_task(owner_key, task_or_client_id) -> TaskSnapshot | None`
- Produces: `list_tasks(owner_key, ids) -> list[TaskSnapshot]`
- Produces: `claim_next_job(worker_id, account_candidates, account_concurrency, now) -> ClaimedJob | None`
- Produces: `checkpoint_job(claim, checkpoint) -> bool`
- Produces: `heartbeat_claims(worker_id, claims, now) -> int`
- Produces: `complete_job(claim, artifact, result) -> TaskSnapshot`
- Produces: `schedule_retry(claim, failure, next_retry_at) -> TaskSnapshot`
- Produces: `fail_job(claim, failure) -> TaskSnapshot`
- Produces: `request_cancel(owner_key, task_or_client_id) -> TaskSnapshot`
- Produces: `is_cancel_requested(task_id) -> bool`
- Produces: `release_claim(claim) -> None`
- Produces: `queue_snapshot() -> dict[str, object]`

- [ ] **Step 1: 写入队、全 Job 成功和旧租约拒写的失败测试**

```python
def test_enqueue_splits_n_into_single_image_jobs(repository, generation_request):
    request = replace(generation_request, required_jobs=4)
    result = repository.enqueue_task(request)
    jobs = repository.list_jobs(result.task.id)
    assert [job.ordinal for job in jobs] == [1, 2, 3, 4]
    assert all(job.status == JobStatus.QUEUED for job in jobs)


def test_same_key_with_different_hash_conflicts(repository, generation_request):
    repository.enqueue_task(generation_request)
    changed = replace(generation_request, request_hash="f" * 64)
    with pytest.raises(IdempotencyConflict):
        repository.enqueue_task(changed)


def test_task_succeeds_only_after_every_job_succeeds(repository, claimed_jobs, artifact_descriptor):
    first = repository.complete_job(claimed_jobs[0], artifact_descriptor, {"url": "https://x/1.png"})
    assert first.status == TaskStatus.RUNNING
    second = repository.complete_job(claimed_jobs[1], artifact_descriptor, {"url": "https://x/2.png"})
    assert second.status == TaskStatus.SUCCESS


def test_expired_lease_cannot_overwrite_reclaimed_job(repository, expired_claim, replacement_claim, artifact_descriptor):
    assert repository.complete_job(expired_claim, artifact_descriptor, {"url": "https://x/stale.png"}) is None
    current = repository.complete_job(replacement_claim, artifact_descriptor, {"url": "https://x/current.png"})
    assert current.status == TaskStatus.SUCCESS


def test_cancel_keeps_completed_artifacts_and_stops_queued_jobs(repository, mixed_task):
    canceled = repository.request_cancel(mixed_task.owner_key, mixed_task.id)
    assert canceled.status == TaskStatus.CANCELED
    assert repository.list_artifacts(mixed_task.id)[0].status == "ready"
    assert all(job.status == JobStatus.CANCELED for job in repository.list_jobs(mixed_task.id) if job.status == JobStatus.QUEUED)
```

- [ ] **Step 2: 运行 SQLite 行为测试并确认失败**

Run: `uv run pytest tests/image_queue/test_repository.py -v`

Expected: Repository 尚不存在。

- [ ] **Step 3: 实现短事务 Repository**

`enqueue_task()` 在一个事务内完成 Task、`required_jobs` 个 Job 和 `task_queued` 事件。所有终态变更在同一事务中写事件、更新计数并重新聚合 Task 状态。

PostgreSQL 领取语句使用：

```python
job = session.execute(
    select(ImageJob)
    .where(ImageJob.status.in_(["queued", "retry_wait"]))
    .where(ImageJob.available_at <= now)
    .order_by(ImageJob.available_at, ImageJob.created_at, ImageJob.ordinal)
    .with_for_update(skip_locked=True)
    .limit(1)
).scalar_one_or_none()
```

账号槽位按候选账号顺序执行 `INSERT INTO image_account_leases (account_id, slot_no, job_id, lease_owner, lease_token, lease_version, expires_at, heartbeat_at) VALUES (:account_id, :slot_no, :job_id, :lease_owner, :lease_token, :lease_version, :expires_at, :heartbeat_at) ON CONFLICT (account_id, slot_no) DO NOTHING RETURNING slot_no`。VALUES 由 SQLAlchemy 绑定参数提供，不拼接 SQL 字符串。领取成功后 Job 写入 `lease_token`、递增 `lease_version` 并设置过期时间。所有检查点和终态更新的 `WHERE` 条件同时匹配 Job ID、Lease Token 和 Lease Version；匹配数为零时返回 `False` 或 `None`，不能写事件或 Artifact。

- [ ] **Step 4: 运行 Repository 单元测试**

Run: `uv run pytest tests/image_queue/test_repository.py -v`

Expected: 全部通过。

- [ ] **Step 5: 用临时 PostgreSQL 验证 SKIP LOCKED 和账号槽位竞争**

Run: `docker compose -f tests/fixtures/docker-compose.postgres.yml up -d`

Run: `$env:TEST_IMAGE_QUEUE_DATABASE_URL='postgresql+psycopg2://postgres:test@127.0.0.1:55432/chatgpt2api_test'; uv run pytest tests/image_queue/test_repository_postgres.py -v`

Run: `docker compose -f tests/fixtures/docker-compose.postgres.yml down -v`

Expected: 两个并发 Session 不会领取同一个 Job，且同一账号不能超过配置槽位数。

- [ ] **Step 6: 提交 Repository**

```powershell
git add services/image_queue/repository.py tests/image_queue/test_repository.py tests/image_queue/test_repository_postgres.py
git commit -m "feat(image): add durable job repository"
```

### Task 4: 输入和最终 Artifact 的严格校验与原子保存

**Files:**
- Create: `services/image_queue/artifact_service.py`
- Create: `tests/image_queue/test_artifact_service.py`
- Modify: `services/image_storage_service.py:52-268`
- Modify: `utils/image_tokens.py:49-59`
- Modify: `api/image_inputs.py:1-310`
- Create: `tests/api/test_image_inputs.py`

**Interfaces:**
- Produces: `ArtifactService.persist_input(task_id, payload, filename, mime_type) -> ArtifactDescriptor`
- Produces: `ArtifactService.persist_final(task_id, job_id, payload, base_url) -> ArtifactDescriptor`
- Produces: `ImageStorageService.save_at_path(relative_path, payload, base_url) -> StoredImage`
- Produces: `verify_image_bytes(payload) -> VerifiedImage`

- [ ] **Step 1: 写无效图片、稳定路径和放大回退的失败测试**

```python
def test_invalid_image_bytes_are_rejected(artifact_service):
    with pytest.raises(InvalidImageArtifact):
        artifact_service.persist_input(uuid4(), b"not-image", "bad.png", "image/png")


def test_final_artifact_is_png_and_uses_content_hash(artifact_service, jpeg_bytes):
    task_id, job_id = uuid4(), uuid4()
    artifact = artifact_service.persist_final(task_id, job_id, jpeg_bytes, "https://img.example")
    stored = artifact.absolute_path.read_bytes()
    assert artifact.relative_path == f"{task_id}/{job_id}/{hashlib.sha256(stored).hexdigest()}.png"
    assert stored.startswith(b"\x89PNG\r\n\x1a\n")
    assert (artifact.width, artifact.height) == (64, 32)


def test_atomic_save_never_publishes_empty_destination(artifact_service, broken_replace, png_bytes):
    with pytest.raises(OSError):
        artifact_service.persist_final(uuid4(), uuid4(), png_bytes, "https://img.example")
    assert list(artifact_service.root.rglob("*.png")) == []
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/image_queue/test_artifact_service.py -v`

Expected: ArtifactService 不存在。

- [ ] **Step 3: 实现严格校验、PNG 转码和同目录原子替换**

实现顺序固定为：

```python
with Image.open(BytesIO(payload)) as probe:
    probe.verify()
with Image.open(BytesIO(payload)) as decoded:
    decoded.load()
    width, height = decoded.size
    normalized = decoded.convert("RGBA") if "A" in decoded.getbands() else decoded.convert("RGB")
    normalized.save(buffer, format="PNG", optimize=True)
```

随后在最终目录创建唯一临时文件，写入、`flush()`、`os.fsync()`，再次 Pillow 校验，最后 `os.replace()`。数据库 Artifact 记录在文件成功后写入；如果数据库提交失败，恢复器根据稳定路径和哈希补齐。WebDAV 模式先完成本地权威文件，再上传验证后的 PNG；`both` 模式要求两端成功后才能 ready。

上传、Base64 和远程输入统一限制 50 MiB，并复用现有远程 URL SSRF 校验。

- [ ] **Step 4: 运行 Artifact 和图片输入测试**

Run: `uv run pytest tests/image_queue/test_artifact_service.py tests/api/test_image_inputs.py -v`

Expected: 全部通过。

- [ ] **Step 5: 提交 Artifact 层**

```powershell
git add services/image_queue/artifact_service.py services/image_storage_service.py utils/image_tokens.py api/image_inputs.py tests/image_queue/test_artifact_service.py tests/api/test_image_inputs.py
git commit -m "feat(image): persist verified image artifacts"
```

### Task 5: 稳定账号 ID 和数据库账号租约

**Files:**
- Modify: `services/account_service.py:110-1280`
- Create: `tests/image_queue/test_account_leases.py`

**Interfaces:**
- Produces: `AccountService.list_image_account_candidates(plan_type=None, source_type=None, plan_types=None, excluded_account_ids=None) -> list[ImageAccountCandidate]`
- Produces: `AccountService.prepare_image_account(account_id) -> str`
- Produces: `AccountService.get_account_by_id(account_id) -> dict | None`
- Produces: `AccountService.record_managed_image_result(account_id, success, failure, error, quota_consumed) -> None`

- [ ] **Step 1: 写 Token 轮换后账号 ID 不变和繁忙账号不占线程的失败测试**

```python
def test_account_id_survives_access_token_rotation(account_service, stored_account):
    before = account_service.get_account(stored_account["access_token"])["account_id"]
    account_service._apply_refreshed_tokens(
        stored_account["access_token"],
        {"access_token": "rotated-token", "refresh_token": stored_account["refresh_token"]},
        "test",
        expected_access_token=stored_account["access_token"],
        expected_refresh_token=stored_account["refresh_token"],
    )
    after = account_service.get_account("rotated-token")["account_id"]
    assert after == before


def test_candidate_listing_does_not_increment_in_memory_slots(account_service):
    candidates = account_service.list_image_account_candidates()
    assert candidates
    assert all(item.get("image_inflight", 0) == 0 for item in account_service.list_accounts())
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/image_queue/test_account_leases.py -v`

Expected: `account_id` 和新候选接口不存在。

- [ ] **Step 3: 实现账号 ID 归一化与无阻塞候选接口**

`_normalize_account()` 对缺少 ID 的账号生成 `uuid4().hex` 并通过现有 `_save_accounts()` 持久化。Token 刷新、导入、合并和状态更新必须保留原 `account_id`。候选接口只返回本地可选账号快照，不读取 `_image_inflight`、不等待 Condition、不提前占槽。

Worker 通过 Repository 取得账号租约后调用 `prepare_image_account(account_id)` 执行现有刷新和远程预检。结果记账复用 `mark_image_result` 的状态更新逻辑，但不得再次释放 `_image_inflight`。

- [ ] **Step 4: 运行账号测试**

Run: `uv run pytest tests/image_queue/test_account_leases.py -v`

Expected: 全部通过。

- [ ] **Step 5: 提交账号租约适配**

```powershell
git add services/account_service.py tests/image_queue/test_account_leases.py
git commit -m "feat(image): use stable account leases"
```

### Task 6: 单图上游执行器和可恢复检查点

**Files:**
- Modify: `services/protocol/conversation.py:465-2750`
- Modify: `services/protocol/openai_v1_image_generations.py:15-53`
- Modify: `services/protocol/openai_v1_image_edit.py:52-103`
- Create: `tests/protocol/conftest.py`
- Create: `tests/protocol/test_image_job_executor.py`

**Interfaces:**
- Produces: `generate_single_image_for_job(request: ConversationRequest) -> list[ImageOutput]`
- Extends: `ConversationRequest.managed_access_token`
- Extends: `ConversationRequest.managed_account_id`
- Extends: `ConversationRequest.checkpoint_callback`
- Extends: `ConversationRequest.image_result_formatter`
- Extends: `ConversationRequest.defer_conversation_cleanup`
- Extends: `ConversationRequest.durable_context`

- [ ] **Step 1: 写固定账号、检查点先于下载和延后清理的失败测试**

```python
def test_managed_job_uses_claimed_account_without_pool_wait(conversation_harness):
    request = ConversationRequest(model="gpt-image-2", prompt="cat", n=1, managed_access_token="claimed-token")
    outputs = generate_single_image_for_job(request)
    assert outputs[0].kind == "result"
    assert conversation_harness.selected_tokens == ["claimed-token"]
    assert conversation_harness.pool_selection_calls == 0


def test_remote_checkpoint_is_written_before_download(conversation_harness):
    order = []
    request = conversation_harness.request(
        checkpoint_callback=lambda checkpoint: order.append(("checkpoint", checkpoint.image_urls)),
        image_result_formatter=lambda payload, context: order.append(("format", len(payload))) or conversation_harness.formatted_result,
    )
    generate_single_image_for_job(request)
    assert order[0][0] == "checkpoint"
    assert order[1][0] == "format"


def test_managed_job_does_not_remove_conversation_before_commit(conversation_harness):
    request = conversation_harness.request(defer_conversation_cleanup=True)
    generate_single_image_for_job(request)
    assert conversation_harness.removed_conversation_ids == []
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/protocol/test_image_job_executor.py -v`

Expected: ConversationRequest 缺少托管字段，单图入口不存在。

- [ ] **Step 3: 抽出托管单图执行路径**

托管路径固定 `n=1`，直接使用租约指定 Token；队列级重试负责换账号。Conversation 获得 `conversation_id`、`file_ids`、`sediment_ids`、`image_urls` 时立即调用检查点。结果格式化通过注入回调交给 ArtifactService，禁止托管路径调用旧的时间戳文件名保存逻辑。

现有 `stream_image_outputs_with_pool()` 保留为内部兼容入口，但外部请求带 `durable_context` 时转入持久任务等待器；不再为外部请求创建 `ThreadPoolExecutor(max_workers=n)`。上游 Conversation 清理函数只在 Worker 完成 Artifact 与数据库事务后调用。

- [ ] **Step 4: 运行协议测试**

Run: `uv run pytest tests/protocol/test_image_job_executor.py -v`

Expected: 全部通过。

- [ ] **Step 5: 提交单图执行器**

```powershell
git add services/protocol/conversation.py services/protocol/openai_v1_image_generations.py services/protocol/openai_v1_image_edit.py tests/protocol/test_image_job_executor.py
git commit -m "refactor(image): expose recoverable single job executor"
```

### Task 7: 分阶段重试、资源控制、恢复器和 Worker Manager

**Files:**
- Create: `services/image_queue/retry_policy.py`
- Create: `services/image_queue/resource_controller.py`
- Create: `services/image_queue/recovery.py`
- Create: `services/image_queue/worker.py`
- Create: `tests/image_queue/test_retry_policy.py`
- Create: `tests/image_queue/test_resource_controller.py`
- Create: `tests/image_queue/test_recovery.py`
- Create: `tests/image_queue/test_worker.py`

**Interfaces:**
- Produces: `RetryPolicy.decision(stage, attempts, error, now) -> RetryDecision`
- Produces: `ResourceController.sample() -> ResourceSnapshot`
- Produces: `ResourceController.allow_new_generation(snapshot) -> ResourceDecision`
- Produces: `ImageRecovery.recover(now) -> RecoverySummary`
- Produces: `ImageWorkerManager.start()`, `stop(timeout)`, `notify()`

- [ ] **Step 1: 写重试分类和 95/85 滞回测试**

```python
@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_transient_upstream_errors_retry(status_code, retry_policy):
    decision = retry_policy.decision("generating", 1, UpstreamHTTPError(status_code), fixed_now)
    assert decision.retry is True
    assert decision.next_retry_at > fixed_now


def test_text_without_image_is_terminal(retry_policy):
    decision = retry_policy.decision("generating", 1, ImageGenerationError("text", failure=image_failure("no_image_generated")), fixed_now)
    assert decision.retry is False


def test_cpu_gate_requires_pause_and_resume_thresholds(resource_controller):
    assert resource_controller.allow_new_generation(snapshot(cpu=96)).allowed is False
    assert resource_controller.allow_new_generation(snapshot(cpu=90)).allowed is False
    assert resource_controller.allow_new_generation(snapshot(cpu=84)).allowed is True


def test_swap_io_is_pressure_not_capacity(resource_controller):
    decision = resource_controller.allow_new_generation(snapshot(cpu=40, swap_in_bytes_per_second=8 * 1024 * 1024))
    assert decision.allowed is False
    assert decision.reason == "resource_swap"
```

- [ ] **Step 2: 写崩溃检查点恢复和线程上限测试**

```python
def test_recovery_resumes_download_when_remote_urls_exist(recovery, expired_remote_job):
    summary = recovery.recover(fixed_now)
    restored = recovery.repository.get_job(expired_remote_job.id)
    assert summary.requeued == 1
    assert restored.stage == JobStage.DOWNLOADING


def test_thousand_queued_jobs_do_not_create_thousand_threads(worker_manager, repository, generation_request):
    for index in range(1000):
        repository.enqueue_task(replace(generation_request, idempotency_key=f"load-{index}", client_task_id=f"load-{index}"))
    worker_manager.start()
    worker_manager.wait_until_saturated()
    assert worker_manager.executor_thread_count <= worker_manager.internal_thread_cap
    assert worker_manager.internal_thread_cap < 1000
```

- [ ] **Step 3: 运行测试并确认失败**

Run: `uv run pytest tests/image_queue/test_retry_policy.py tests/image_queue/test_resource_controller.py tests/image_queue/test_recovery.py tests/image_queue/test_worker.py -v`

Expected: 四个组件不存在。

- [ ] **Step 4: 实现重试、资源采样、心跳和 Worker 生命周期**

重试预算为生成 3、下载 5、保存 5；退避使用 `min(300, 5 * 3 ** (attempt - 1)) + uniform(0, 1)` 秒。ResourceController 使用 cgroup v2 `cpu.max`、`memory.max`，失败时回退 psutil；采集 Swap 换入换出速率并只把它作为压力信号，同时采集线程数和文件句柄数。连续压力采样暂停，连续恢复采样才放行。数据库连接池占用超过 85%、磁盘不足 5 GiB 或 5% 时阻止新生成。

Worker Manager 创建有界生成、I/O、放大和注册执行资源，调度器只在有效槽位与资源门控允许时领取。独立心跳循环为所有活动 Claim 续租。Worker 在领取后和每个阶段边界检查 `cancel_requested`；已经生成的图片仍完成幂等保存，但不再启动新的上游调用。任何 Worker 异常都通过 RetryPolicy 转为 `retry_wait` 或 `failed`，finally 中释放账号租约；不能吞异常。

恢复器按 final Artifact、下载/放大 Artifact、远程检查点、重新生成的顺序恢复，并把每次决定写入事件表。

- [ ] **Step 5: 运行 Worker 组件测试**

Run: `uv run pytest tests/image_queue/test_retry_policy.py tests/image_queue/test_resource_controller.py tests/image_queue/test_recovery.py tests/image_queue/test_worker.py -v`

Expected: 全部通过。

- [ ] **Step 6: 提交 Worker 系统**

```powershell
git add services/image_queue/retry_policy.py services/image_queue/resource_controller.py services/image_queue/recovery.py services/image_queue/worker.py tests/image_queue/test_retry_policy.py tests/image_queue/test_resource_controller.py tests/image_queue/test_recovery.py tests/image_queue/test_worker.py
git commit -m "feat(image): add adaptive durable workers"
```

### Task 8: PostgreSQL ImageTaskService、API 入队和应用生命周期

**Files:**
- Rewrite: `services/image_task_service.py`
- Modify: `api/image_tasks.py:1-220`
- Modify: `api/ai.py:74-192`
- Modify: `api/app.py:50-76`
- Create: `tests/services/conftest.py`
- Create: `tests/services/test_image_task_service.py`
- Create: `tests/api/conftest.py`
- Create: `tests/api/test_image_queue_routes.py`

**Interfaces:**
- Preserves: `submit_generation()`, `submit_edit()`, `list_tasks()`, `resume_poll()`
- Produces: `submit_protocol_request(identity, payload, mode, idempotency_key, trace_headers) -> TaskSnapshot`
- Produces: `wait_for_terminal(owner_key, task_id, timeout=None) -> TaskSnapshot`
- Produces: `acknowledge(identity, task_id) -> TaskSnapshot`
- Produces: `cancel(identity, task_id) -> TaskSnapshot`
- Produces: `start()`, `stop(timeout)`

- [ ] **Step 1: 写只入队、断线继续和幂等复用的失败测试**

```python
def test_async_submit_returns_before_worker_runs(image_task_service, stopped_worker):
    result = image_task_service.submit_generation(identity, client_task_id="client-1", prompt="cat", model="gpt-image-2")
    assert result["status"] == "queued"
    assert result["task_id"]
    assert result["client_task_id"] == "client-1"
    assert stopped_worker.execution_count == 0


def test_protocol_waiter_cancellation_does_not_cancel_task(image_task_service, queued_task):
    waiter = image_task_service.create_waiter(queued_task.task_id)
    waiter.close()
    assert image_task_service.get_task(identity, queued_task.task_id)["status"] == "queued"


def test_same_newapi_request_returns_saved_result(client, completed_task, auth_headers):
    headers = {**auth_headers, "Idempotency-Key": "newapi-1"}
    first = client.post("/v1/images/generations", headers=headers, json={"model": "gpt-image-2", "prompt": "cat"})
    second = client.post("/v1/images/generations", headers=headers, json={"model": "gpt-image-2", "prompt": "cat"})
    assert first.json() == second.json()
    assert completed_task.generation_count == 1


def test_cancel_endpoint_is_idempotent_and_does_not_delete_result(client, partially_completed_task, auth_headers):
    first = client.post(f"/api/image-tasks/{partially_completed_task.id}/cancel", headers=auth_headers)
    second = client.post(f"/api/image-tasks/{partially_completed_task.id}/cancel", headers=auth_headers)
    assert first.json()["status"] == second.json()["status"] == "canceled"
    assert partially_completed_task.final_artifact_path.exists()
```

- [ ] **Step 2: 运行测试并确认旧服务行为失败**

Run: `uv run pytest tests/services/test_image_task_service.py tests/api/test_image_queue_routes.py -v`

Expected: 旧服务仍写 JSON、启动任务线程，且同步接口未带 durable context。

- [ ] **Step 3: 重写服务门面并接入 API**

服务构造时不连接数据库；FastAPI lifespan 中按以下顺序启动：数据库迁移、旧任务导入、过期租约恢复、Worker Manager、其他现有后台服务。关闭时先停止领取，再等待活动 Job 安全停在检查点，最后释放数据库连接。

所有外部图片请求在 `api/ai.py` 注入：

```python
payload["_image_task_context"] = {
    "identity": identity,
    "idempotency_key": select_idempotency_key(request.headers, ""),
    "trace_headers": allowlisted_trace_headers(request.headers),
    "base_url": resolve_image_base_url(request),
}
```

Responses 路由增加 `request: Request` 并调用现有 trace 头提取。`/api/image-tasks/*` 返回近似 Job 排位、预计开始/结束区间和固定 wait reason code。新增任务取消、ACK 与按幂等键查询接口；取消只设置控制状态并保留已有 Artifact。`resume_poll()` 改为将可恢复 Job 重新置为可执行，不创建线程。

- [ ] **Step 4: 运行服务和 API 测试**

Run: `uv run pytest tests/services/test_image_task_service.py tests/api/test_image_queue_routes.py -v`

Expected: 全部通过。

- [ ] **Step 5: 提交服务与路由切换**

```powershell
git add services/image_task_service.py api/image_tasks.py api/ai.py api/app.py tests/services/test_image_task_service.py tests/api/test_image_queue_routes.py
git commit -m "feat(image): route external images through postgres"
```

### Task 9: Images、Chat、Responses 与 Stream 结果适配

**Files:**
- Modify: `services/protocol/openai_v1_image_generations.py`
- Modify: `services/protocol/openai_v1_image_edit.py`
- Modify: `services/protocol/openai_v1_chat_complete.py:238-326`
- Modify: `services/protocol/openai_v1_response.py:212-439`
- Modify: `services/protocol/conversation.py:539-588,2625-2748`
- Create: `tests/protocol/test_durable_image_protocols.py`

**Interfaces:**
- Produces: `task_snapshot_to_outputs(snapshot, response_format) -> Iterator[ImageOutput]`
- Extends: Chat 非流式 `message.image_results`
- Extends: Chat 流式最终 chunk `image_results`
- Extends: Responses 图片 Item `width`、`height`

- [ ] **Step 1: 写四种协议实际尺寸和全 Job 成功语义的失败测试**

```python
def test_images_response_uses_final_artifact_dimensions(protocol_harness):
    response = protocol_harness.images_response(width=2048, height=1024)
    assert response["data"][0]["width"] == 2048
    assert response["data"][0]["height"] == 1024


def test_chat_response_keeps_markdown_and_adds_image_results(protocol_harness):
    response = protocol_harness.chat_response(width=1024, height=1024)
    message = response["choices"][0]["message"]
    assert "![" in message["content"]
    assert message["image_results"][0]["width"] == 1024


def test_responses_item_carries_final_dimensions(protocol_harness):
    item = protocol_harness.responses_result(width=1536, height=1024)["output"][0]
    assert (item["width"], item["height"]) == (1536, 1024)


def test_one_failed_required_job_returns_task_failure(protocol_harness):
    with pytest.raises(ImageGenerationError):
        protocol_harness.await_task(job_statuses=["success", "failed"])
    assert protocol_harness.saved_artifact_count == 1
```

- [ ] **Step 2: 运行测试并确认尺寸丢失和部分成功问题**

Run: `uv run pytest tests/protocol/test_durable_image_protocols.py -v`

Expected: Chat/Responses 缺少尺寸，旧池允许部分成功。

- [ ] **Step 3: 实现协议无关结果到现有响应格式的转换**

持久任务结果按 Job ordinal 排序。URL 与 Base64 均从 ready final Artifact 读取；每项始终包含实际 `width`、`height`。Stream 只在 Task 成功后生成现有协议的最终事件；排队过程不发送非标准 OpenAI 事件。失败 Task 使用现有 ImageFailure 公开错误转换，成功 Artifact 仍可从任务查询接口访问。

- [ ] **Step 4: 运行全部协议测试**

Run: `uv run pytest tests/protocol/test_durable_image_protocols.py tests/protocol/test_image_job_executor.py tests/protocol/test_image_model_policy.py -v`

Expected: 全部通过。

- [ ] **Step 5: 提交协议适配**

```powershell
git add services/protocol tests/protocol/test_durable_image_protocols.py
git commit -m "feat(image): return durable results across protocols"
```

### Task 10: 峰谷补号、PostgreSQL 监控、备份和前端展示

**Files:**
- Modify: `services/register_service.py:75-414`
- Modify: `api/register.py:12-32`
- Modify: `services/realtime_monitor_service.py:171-774`
- Modify: `services/backup_service.py:611-650`
- Modify: `services/config.py:19-40,623-655`
- Modify: `web-vue/src/api/register.ts`
- Modify: `web-vue/src/api/monitor.ts`
- Modify: `web-vue/src/views/Register.vue`
- Modify: `web-vue/src/views/register/RegisterTaskSettingsPanel.vue`
- Modify: `web-vue/src/views/register/registerProviderView.ts`
- Modify: `web-vue/src/views/Monitor.vue`
- Modify: `web-vue/src/views/monitor/monitorView.ts`
- Create: `tests/services/test_register_schedule.py`
- Create: `tests/services/test_queue_monitor_backup.py`

**Interfaces:**
- Produces: `resolve_registration_window(now) -> RegistrationWindow`
- Produces: `RealtimeMonitorService.set_image_queue_provider(provider)`
- Produces: `ImageQueueRepository.logical_backup() -> dict[str, object]`
- Produces: `ConfigStore.set_image_retention_protection(provider)`

- [ ] **Step 1: 写跨午夜、资源保护和数据库监控失败测试**

```python
@pytest.mark.parametrize(
    ("hour", "target", "threads"),
    [(18, 100, 4), (1, 100, 4), (2, 30, 2), (17, 30, 2)],
)
def test_registration_window_crosses_midnight(hour, target, threads, register_service):
    window = register_service.resolve_registration_window(beijing_datetime(hour=hour))
    assert (window.target_available, window.threads) == (target, threads)


def test_registration_pauses_while_image_resources_are_blocked(register_service, blocked_resource_controller):
    assert register_service.should_submit_registration() is False


def test_monitor_reports_postgres_queue_and_terminal_window(realtime_monitor_service, queue_snapshot):
    realtime_monitor_service.set_image_queue_provider(lambda: queue_snapshot)
    snapshot = realtime_monitor_service.snapshot()
    assert snapshot["image_queue"]["queued"] == 12
    assert snapshot["window"]["label"] == "结束窗口"


def test_backup_uses_logical_database_export_not_legacy_json(backup_service, archive_names):
    backup_service._build_backup_archive(backup_settings, trigger="test")
    assert "data/image-queue.json" in archive_names
    assert "data/image_tasks.json" not in archive_names


def test_retention_cleanup_preserves_unacknowledged_artifact(config_store, old_unacknowledged_image):
    config_store.set_image_retention_protection(lambda: {old_unacknowledged_image.relative_path})
    config_store.cleanup_old_images()
    assert old_unacknowledged_image.absolute_path.exists()
```

- [ ] **Step 2: 运行后端测试并确认失败**

Run: `uv run pytest tests/services/test_register_schedule.py tests/services/test_queue_monitor_backup.py -v`

Expected: 峰谷配置和 Queue Provider 不存在，备份仍读取 JSON 文件。

- [ ] **Step 3: 实现峰谷配置、资源门控、数据库聚合和逻辑备份**

默认配置固定为：

```python
REGISTER_PEAK = {"time_range": "18:00-02:00", "target_available": 100, "threads": 4}
REGISTER_OFFPEAK = {"time_range": "02:00-18:00", "target_available": 30, "threads": 2}
```

`RegisterConfigRequest` 增加 `register_peak: dict | None` 与 `register_offpeak: dict | None`，服务端归一化时只接受 `time_range`、`target_available` 和 `threads` 三个字段，并校验两个时间段完整覆盖 24 小时且不重叠。

`target_available` 使用 `evaluate_account_pool()` 的健康远程确认账号数，不扣除正在生图的账号。注册控制器每轮提交前检查 ResourceController；被阻止时记录原因并等待下一轮。

监控保留现有文本调用窗口，并增加 PostgreSQL 图片队列聚合。原 completed 展示文案统一改为“结束窗口”，拆分 success、failed、canceled，显示 queued/running/saving/retrying、最老排队时间、P90、租约、Worker 心跳和未 ACK 成功数。

图片清理每次先从 Repository 取得活动任务及未确认成功任务的 Artifact 路径集合。`ConfigStore.cleanup_old_images()` 对这些路径无条件跳过；磁盘压力只暂停新任务并告警，不能绕过保护集合删除图片。

备份中的 `image_tasks` 开关保持 API 兼容，但内容改为 Repository 的脱敏逻辑导出和 Artifact 清单。

- [ ] **Step 4: 更新前端类型与表单并构建**

Run: `npm --prefix web-vue run build`

Expected: Vue/TypeScript 构建成功，峰谷配置与 PostgreSQL 队列统计可显示。

- [ ] **Step 5: 运行后端测试**

Run: `uv run pytest tests/services/test_register_schedule.py tests/services/test_queue_monitor_backup.py -v`

Expected: 全部通过。

- [ ] **Step 6: 提交监控、注册与备份**

```powershell
git add services/register_service.py api/register.py services/realtime_monitor_service.py services/backup_service.py services/config.py web-vue tests/services/test_register_schedule.py tests/services/test_queue_monitor_backup.py
git commit -m "feat(image): expose queue health and protected registration"
```

### Task 11: 旧任务导入、故障注入、全链路验收和部署文档

**Files:**
- Modify: `services/image_queue/recovery.py`
- Modify: `services/image_task_service.py`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.local.yml`
- Modify: `config.example.yaml`
- Modify: `README.md`
- Create: `tests/integration/conftest.py`
- Create: `tests/image_queue/test_legacy_migration.py`
- Create: `tests/integration/test_image_queue_fault_recovery.py`
- Create: `tests/integration/test_image_queue_load.py`

**Interfaces:**
- Produces: `ImageRecovery.import_legacy_tasks(path) -> LegacyImportSummary`
- Verifies: 一次切换后无活动 JSON 读写、重启恢复和下游幂等取回。

- [ ] **Step 1: 写旧任务迁移和四个崩溃窗口的失败测试**

```python
def test_legacy_terminal_task_is_imported_and_unfinished_task_is_interrupted(recovery, legacy_task_file):
    summary = recovery.import_legacy_tasks(legacy_task_file)
    assert summary.imported_terminal == 1
    assert summary.interrupted == 1
    interrupted = recovery.repository.get_task_by_client_id("owner", "running-old")
    assert interrupted.error_code == "legacy_interrupted"


@pytest.mark.parametrize(
    "crash_point",
    ["after_remote_checkpoint", "after_download", "after_atomic_replace", "after_database_commit"],
)
def test_restart_recovers_without_losing_saved_image(crash_point, fault_harness):
    task_id = fault_harness.run_until_crash(crash_point)
    recovered = fault_harness.restart_and_wait(task_id)
    assert recovered.status == "success"
    assert recovered.data[0]["width"] > 0
    assert fault_harness.final_file_is_valid(task_id)
```

- [ ] **Step 2: 运行迁移和故障测试并确认失败**

Run: `uv run pytest tests/image_queue/test_legacy_migration.py tests/integration/test_image_queue_fault_recovery.py -v`

Expected: 旧 JSON 导入器和故障恢复 Harness 未实现。

- [ ] **Step 3: 完成一次性迁移与部署配置**

旧 JSON 只读取一次：终态记录映射到新 Task；queued/running 记录以 `legacy_interrupted` 终止。导入成功写入数据库迁移事件和文件哈希，后续启动发现相同哈希时跳过；原文件保持只读，不删除。

Compose 增加 `IMAGE_QUEUE_DATABASE_URL`、PostgreSQL healthcheck 和图片持久卷。README 明确：图片功能要求 PostgreSQL；多服务器共享负载均衡时必须请求粘性或共享数据库与共享 Artifact 存储；下游必须传稳定 `Idempotency-Key` 或 `X-NewAPI-Request-Id`。

- [ ] **Step 4: 执行 1000 Task 有界线程负载测试**

Run: `uv run pytest tests/integration/test_image_queue_load.py -v`

Expected: 1000 Task 全部持久入队，进程线程数不超过 Worker 内部安全上限，没有一 Task 一线程。

- [ ] **Step 5: 执行完整后端测试、PostgreSQL 集成测试和前端构建**

Run: `uv run pytest -v`

Run: `docker compose -f tests/fixtures/docker-compose.postgres.yml up -d`

Run: `$env:TEST_IMAGE_QUEUE_DATABASE_URL='postgresql+psycopg2://postgres:test@127.0.0.1:55432/chatgpt2api_test'; uv run pytest tests/image_queue/test_repository_postgres.py tests/integration -v`

Run: `docker compose -f tests/fixtures/docker-compose.postgres.yml down -v`

Run: `npm --prefix web-vue run build`

Expected: 全部命令退出码为 0，无失败和未处理警告。

- [ ] **Step 6: 检查差异和敏感信息**

Run: `git diff --check`

Run: `rg -n "Authorization|access_token|refresh_token|Cookie" services/image_queue tests | Select-String -NotMatch "redact|forbidden|test secret|field name"`

Expected: diff 检查无输出；扫描结果不包含把真实凭据写入事件、Task 或日志的代码。

- [ ] **Step 7: 提交最终迁移与验收**

```powershell
git add services/image_queue/recovery.py services/image_task_service.py docker-compose.yml docker-compose.local.yml config.example.yaml README.md tests/image_queue/test_legacy_migration.py tests/integration/test_image_queue_fault_recovery.py tests/integration/test_image_queue_load.py
git commit -m "feat(image): complete durable queue cutover"
```

- [ ] **Step 8: 最终需求逐项核对**

逐项对照设计文档第 16 节的 13 条验收标准，记录对应测试名和最新执行结果。确认工作树没有临时数据库文件、图片、日志、测试导出、密钥或前端构建缓存后再交付。
