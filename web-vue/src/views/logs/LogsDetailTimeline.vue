<template>
  <section class="detail-timeline">
    <div class="detail-timeline__header">
      <div>
        <span class="detail-timeline__title">步骤耗时</span>
        <p>按执行顺序展示，条形长度表示相对耗时</p>
      </div>
      <div class="detail-timeline__meta">
        <MetaChip size="xs" tone="muted">{{ stepCount }} 步</MetaChip>
        <MetaChip v-if="segmentTotalMs" size="xs" tone="muted">
          分段合计 {{ formatTimelineMs(segmentTotalMs) }}
        </MetaChip>
        <MetaChip v-if="bottleneckStep" size="xs" tone="warning">
          瓶颈 {{ bottleneckStep.label }}
        </MetaChip>
      </div>
    </div>

    <LogsTimelineBreakdown
      :segments="segments"
      :legend-items="legendItems"
      :groups="groups"
      :details-visible="detailsVisible"
      empty-message="这条日志没有步骤耗时埋点；新的图片请求会显示分段耗时。"
      @toggle-details="emit('toggle-details')"
    />
  </section>
</template>

<script setup lang="ts">
import MetaChip from '@/components/ai/MetaChip.vue'
import LogsTimelineBreakdown from '@/views/logs/LogsTimelineBreakdown.vue'
import {
  formatTimelineMs,
  type DetailTimelineGroup,
  type DetailTimelineLegendItem,
  type DetailTimelineSegment,
  type DetailTimelineStep,
} from '@/views/logs/logDetailView'

defineProps<{
  segments: DetailTimelineSegment[]
  legendItems: DetailTimelineLegendItem[]
  groups: DetailTimelineGroup[]
  bottleneckStep: DetailTimelineStep | null
  stepCount: number
  segmentTotalMs: number
  detailsVisible: boolean
}>()

const emit = defineEmits<{
  (e: 'toggle-details'): void
}>()
</script>

<style scoped>
.detail-timeline {
  display: flex;
  flex-direction: column;
  gap: 14px;
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
  background: hsl(var(--card));
  padding: 14px;
}

.detail-timeline__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.detail-timeline__title {
  font-size: 13px;
  font-weight: 650;
  color: hsl(var(--foreground));
}

.detail-timeline__header p {
  margin-top: 3px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.detail-timeline__meta {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}

@media (max-width: 640px) {
  .detail-timeline__header {
    flex-direction: column;
  }

  .detail-timeline__meta {
    justify-content: flex-start;
  }
}
</style>
