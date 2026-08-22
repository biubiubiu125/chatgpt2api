<template>
  <div class="timeline-summary">
    <MetaChip size="xs" tone="muted">
      {{ stepCount }} 步
    </MetaChip>
    <MetaChip size="xs" tone="muted">
      {{ durationMs > 0 ? formatLogDuration(durationMs) : '-' }}
    </MetaChip>
    <StateBadge
      :tone="status === 'failed' ? 'danger' : status === 'generated' || status === 'text_review' ? 'warning' : 'success'"
      size="xs"
      shape="rounded"
    >
      {{ status === 'failed' ? '失败' : status === 'generated' ? '生成成功' : status === 'text_review' ? '文本' : '成功' }}
    </StateBadge>
  </div>
</template>

<script setup lang="ts">
import MetaChip from '@/components/ai/MetaChip.vue'
import StateBadge from '@/components/ai/StateBadge.vue'
import { formatLogDuration } from '@/api/logs'

defineProps<{
  stepCount: number
  durationMs: number
  status: 'success' | 'failed' | 'generated' | 'text_review'
}>()
</script>

<style scoped>
.timeline-summary {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
}

@media (max-width: 640px) {
  .timeline-summary {
    justify-content: flex-start;
  }
}
</style>
