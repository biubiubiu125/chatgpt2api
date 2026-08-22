<template>
  <section class="detail-timeline">
    <div class="detail-timeline__header">
      <div>
        <span class="detail-timeline__title">步骤耗时</span>
        <p>按执行顺序展示，条形长度表示相对耗时</p>
      </div>
      <LogsTimelineSummary
        :step-count="stepCount"
        :duration-ms="durationMs"
        :status="status"
      />
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
import LogsTimelineBreakdown from '@/views/logs/LogsTimelineBreakdown.vue'
import LogsTimelineSummary from '@/views/logs/LogsTimelineSummary.vue'
import {
  type DetailTimelineGroup,
  type DetailTimelineLegendItem,
  type DetailTimelineSegment,
} from '@/views/logs/logDetailView'

defineProps<{
  segments: DetailTimelineSegment[]
  legendItems: DetailTimelineLegendItem[]
  groups: DetailTimelineGroup[]
  stepCount: number
  durationMs: number
  status: 'success' | 'failed'
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

@media (max-width: 640px) {
  .detail-timeline__header {
    flex-direction: column;
  }
}
</style>
