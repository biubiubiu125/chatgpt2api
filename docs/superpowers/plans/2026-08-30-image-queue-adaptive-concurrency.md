# 独服生图并发自适应提效 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make image queue defaults adapt to the host instead of staying pinned to 64 / 128, while keeping a high outer ceiling and the existing resource safety rails.

**Architecture:** Add a small adaptive-default layer inside `services/image_queue/settings.py` so direct construction and `from_env()` both pick the same machine-aware defaults. Keep the worker and resource controller unchanged except for the values they receive. Update the Docker / install / docs surfaces so the default environment matches the new behavior.

**Tech Stack:** Python 3.13, pytest, Docker Compose, Bash, Markdown.

## Global Constraints

- Preserve existing queue semantics, worker stages, and resource-controller safety checks.
- Keep manual environment-variable overrides working.
- Default behavior must be machine-aware and not depend on fixed 64 / 128 values.
- Do not change unrelated ports, domains, or topology.

---

### Task 1: Add adaptive defaults to image queue settings

**Files:**
- Modify: `services/image_queue/settings.py`
- Add/Update: `tests/image_queue/test_settings.py`

**Interfaces:**
- `ImageQueueSettings.__post_init__()`
- `_adaptive_generation_concurrency_default(runtime_limit: int | None = None, cpu_cores: int | None = None, available_memory_bytes: int | None = None)`
- `_adaptive_absolute_guard_default(generation_concurrency: int, cpu_cores: int | None = None)`

- [ ] **Step 1: Write failing tests for auto defaults and overrides**

```python
def test_auto_concurrency_defaults_scale_with_large_hosts(monkeypatch):
    monkeypatch.setenv("IMAGE_QUEUE_DATABASE_URL", "postgresql://queue/chatgpt2api_image_queue")
    monkeypatch.delenv("IMAGE_QUEUE_GENERATION_CONCURRENCY", raising=False)
    monkeypatch.delenv("IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP", raising=False)
    monkeypatch.delenv("IMAGE_QUEUE_ABSOLUTE_GUARD", raising=False)
    monkeypatch.setattr(settings_module, "_detected_cpu_cores", lambda: 64, raising=False)
    monkeypatch.setattr(settings_module, "_detected_available_memory_bytes", lambda: 256 * 1024**3, raising=False)
    monkeypatch.setattr(settings_module.config, "get_runtime_capacity_settings", lambda: {"image_concurrency_limit": 2000})
    settings = ImageQueueSettings.from_env()
    assert settings.generation_concurrency_hard_cap == 99999
    assert settings.generation_concurrency_limit == 128
    assert settings.absolute_guard == 276


def test_concurrency_overrides_inside_hard_limits_are_preserved():
    settings = ImageQueueSettings(
        database_url="postgresql://test",
        generation_concurrency_hard_cap=512,
        generation_concurrency_limit=96,
        absolute_guard=160,
    )
    assert settings.generation_concurrency_hard_cap == 512
    assert settings.generation_concurrency_limit == 96
    assert settings.absolute_guard == 160
```

- [ ] **Step 2: Run the settings tests and confirm they fail**

Run:

```powershell
python -m pytest -q tests/image_queue/test_settings.py
```

Expected: fail because the adaptive-default helpers and `__post_init__` behavior do not exist yet.

- [ ] **Step 3: Implement the minimal adaptive-default logic**

```python
def __post_init__(self) -> None:
    hard_cap = DEFAULT_GENERATION_CONCURRENCY_HARD_CAP if self.generation_concurrency_hard_cap <= 0 else min(self.generation_concurrency_hard_cap, MAX_GENERATION_CONCURRENCY_HARD_CAP)
    generation_limit = _runtime_image_concurrency_default() if self.generation_concurrency_limit <= 0 else self.generation_concurrency_limit
    generation_limit = min(generation_limit, hard_cap)
    absolute_guard = _adaptive_absolute_guard_default(generation_limit) if self.absolute_guard <= 0 else min(self.absolute_guard, MAX_ABSOLUTE_GUARD)
    object.__setattr__(self, "generation_concurrency_hard_cap", hard_cap)
    object.__setattr__(self, "generation_concurrency_limit", generation_limit)
    object.__setattr__(self, "absolute_guard", absolute_guard)
```

- [ ] **Step 4: Re-run the settings tests**

Run:

```powershell
python -m pytest -q tests/image_queue/test_settings.py
```

Expected: pass.

### Task 2: Update compose / installer / docs defaults

**Files:**
- Modify: `docker-compose.yml`
- Modify: `docker-compose.local.yml`
- Modify: `docker-compose.cluster-main.yml`
- Modify: `docker-compose.cluster-worker.yml`
- Modify: `docker-compose.warp.yml`
- Modify: `deploy/install.sh`
- Modify: `.env.example`
- Modify: `README.md`

**Interfaces:**
- Environment defaults expose the new adaptive behavior without forcing 64 / 128.

- [ ] **Step 1: Update the environment defaults**

```yaml
IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP: ${IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP:-99999}
IMAGE_QUEUE_ABSOLUTE_GUARD: ${IMAGE_QUEUE_ABSOLUTE_GUARD:-}
```

- [ ] **Step 2: Update docs and comments**

```markdown
- `IMAGE_QUEUE_GENERATION_CONCURRENCY` 未设置时按 CPU / 内存自动推算。
- `IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP` 默认 99999，只做外层硬阀。
- `IMAGE_QUEUE_ABSOLUTE_GUARD` 未设置时按机器资源自动推算。
```

- [ ] **Step 3: Re-run a focused grep / sanity pass**

Run:

```powershell
rg -n "IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP|IMAGE_QUEUE_ABSOLUTE_GUARD|64|128" README.md .env.example deploy/install.sh docker-compose*.yml
```

Expected: the user-facing defaults no longer advertise fixed 64 / 128.

### Task 3: Verify worker/resource regressions stay green

**Files:**
- Test: `tests/image_queue/test_worker.py`
- Test: `tests/image_queue/test_resource_controller.py`

**Interfaces:**
- `ImageWorkerManager` still keeps the thread pools bounded by the adaptive settings.
- `ResourceController` still rejects pressure conditions and keeps current limiter behavior.

- [ ] **Step 1: Re-run the existing image-queue pool and resource tests**

Run:

```powershell
python -m pytest -q tests/image_queue/test_settings.py tests/image_queue/test_resource_controller.py tests/image_queue/test_worker.py
```

Expected: pass, and the bounded-pool assertions still hold with the adaptive defaults.

- [ ] **Step 2: Final sanity check**

Run:

```powershell
python -m pytest -q tests/image_queue/test_settings.py tests/image_queue/test_resource_controller.py tests/image_queue/test_worker.py tests/services/test_threadpool_governor.py
```

Expected: pass.
