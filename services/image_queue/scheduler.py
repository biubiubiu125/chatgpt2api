from __future__ import annotations

from dataclasses import dataclass

from services.image_queue.types import JobStage, ResourceDecision


GENERATION_STAGES = frozenset({
    JobStage.QUEUED.value,
    JobStage.LEASED.value,
    JobStage.GENERATING.value,
})
RECOVERY_STAGES = frozenset({
    JobStage.RESOLVING.value,
    JobStage.DOWNLOADING.value,
    JobStage.TRANSFORMING.value,
    JobStage.SAVING.value,
})


@dataclass(frozen=True)
class ClaimDispatchPlan:
    allow_generation: bool
    allow_recovery: bool
    generation_limit: int
    pause_reason: str = ""


def is_generation_stage(stage: JobStage | str) -> bool:
    value = stage.value if isinstance(stage, JobStage) else str(stage or "")
    return value in GENERATION_STAGES


def is_recovery_stage(stage: JobStage | str) -> bool:
    value = stage.value if isinstance(stage, JobStage) else str(stage or "")
    return value in RECOVERY_STAGES


def plan_claim_dispatch(
    *,
    generation: ResourceDecision,
    recovery: ResourceDecision,
    active_generation_count: int,
    pending_capacity: int,
    generation_hard_limit: int | None = None,
) -> ClaimDispatchPlan:
    """Decide whether the dispatcher may pull recovery and/or fresh generation jobs."""
    allow_recovery = bool(recovery.allowed) and pending_capacity > 0
    generation_limit = max(0, int(generation.effective_limit)) if generation.allowed else 0
    if generation_hard_limit is not None:
        generation_limit = min(generation_limit, max(0, int(generation_hard_limit)))
    allow_generation = (
        bool(generation.allowed)
        and pending_capacity > 0
        and active_generation_count < generation_limit
    )
    pause_reason = ""
    if not allow_generation and not allow_recovery:
        pause_reason = generation.reason or recovery.reason or "resource_paused"
    elif not allow_generation:
        pause_reason = generation.reason or ""
    return ClaimDispatchPlan(
        allow_generation=allow_generation,
        allow_recovery=allow_recovery,
        generation_limit=generation_limit,
        pause_reason=pause_reason,
    )
