<template>
  <section class="attempt-timeline">
    <button
      type="button"
      class="attempt-timeline__header"
      :aria-expanded="attemptsVisible"
      @click="attemptsVisible = !attemptsVisible"
    >
      <div class="attempt-timeline__heading">
        <span class="attempt-timeline__title">
          <Icon icon="lucide:repeat-2" />
          账号切换
        </span>
        <p>{{ switchSummary }}</p>
      </div>
      <div class="attempt-timeline__summary">
        <MetaChip size="xs" tone="muted">{{ attemptRows.length }} 次尝试</MetaChip>
        <MetaChip v-if="switchCount" size="xs" tone="warning">
          切换 {{ switchCount }} 次
        </MetaChip>
        <span class="attempt-timeline__toggle-label">
          {{ attemptsVisible ? '收起' : '展开' }}
          <Icon
            icon="lucide:chevron-down"
            :class="{ 'attempt-timeline__chevron--open': attemptsVisible }"
          />
        </span>
      </div>
    </button>

    <div v-show="attemptsVisible" class="attempt-timeline__list">
      <div
        v-for="(attempt, index) in attemptRows"
        :key="attempt.key"
        class="attempt-timeline__item"
      >
        <div
          class="attempt-timeline__rail"
          :class="{ 'attempt-timeline__rail--last': isRailLast(index) }"
        >
          <span :class="`attempt-timeline__marker--${attemptTone(attempt)}`">
            <Icon :icon="attempt.status === 'success' ? 'lucide:check' : 'lucide:x'" />
          </span>
        </div>

        <div class="attempt-timeline__content">
          <div v-if="isAccountSwitch(index)" class="attempt-timeline__switch">
            <Icon icon="lucide:repeat-2" />
            切换账号
          </div>

          <div class="attempt-timeline__row">
            <div class="attempt-timeline__identity">
              <strong>图片 {{ attempt.slot }} · 尝试 {{ attempt.attempt }}</strong>
              <span :title="attempt.accountEmail">{{ attempt.accountEmail || '账号未记录' }}</span>
            </div>
            <div class="attempt-timeline__result">
              <span v-if="attempt.durationMs">本次 {{ formatTimelineMs(attempt.durationMs) }}</span>
              <StateBadge :tone="attemptTone(attempt)" size="xs" shape="rounded" :bordered="false">
                {{ attemptStatusLabel(attempt) }}
              </StateBadge>
            </div>
          </div>

          <div v-if="attempt.failureCode" class="attempt-timeline__facts">
            <span class="attempt-timeline__fact">
              <span class="attempt-timeline__fact-label">
                {{ attempt.status === 'success' ? '后续交付失败' : '失败原因' }}
              </span>
              <code>{{ attempt.failureCode }}</code>
            </span>
          </div>

          <div class="attempt-timeline__breakdown">
            <LogsTimelineBreakdown
              :segments="attempt.timelineSegments"
              :legend-items="attempt.key === legendOwnerKey ? attempt.legendItems : []"
              :groups="attempt.timelineGroups"
              :details-visible="isAttemptDetailsVisible(attempt.key)"
              empty-message="这次尝试没有步骤耗时记录。"
              @toggle-details="toggleAttemptDetails(attempt.key)"
            />
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Icon } from '@iconify/vue'
import {
  imageAccountSwitchCount,
  switchedImageAttempts,
  type ImageAttempt,
} from '@/api/logs'
import MetaChip from '@/components/ai/MetaChip.vue'
import StateBadge from '@/components/ai/StateBadge.vue'
import {
  buildAttemptTimelineGroups,
  buildAttemptTimelineSegments,
  buildTimelineLegendItems,
  formatTimelineMs,
} from '@/views/logs/logDetailView'
import LogsTimelineBreakdown from '@/views/logs/LogsTimelineBreakdown.vue'

const props = defineProps<{
  attempts: ImageAttempt[]
}>()

const attemptsVisible = ref(true)
const visibleAttemptDetails = ref<string[]>([])
const switchCount = computed(() => imageAccountSwitchCount(props.attempts))
const switchSummary = '失败后已改用其他账号继续执行'
const switchedAttempts = computed(() => switchedImageAttempts(props.attempts))
const attemptRows = computed(() => switchedAttempts.value.map((attempt) => {
  const timelineSegments = buildAttemptTimelineSegments(attempt.monitor)

  return {
    ...attempt,
    key: attemptKey(attempt),
    timelineSegments,
    timelineGroups: buildAttemptTimelineGroups(attempt.monitor),
    legendItems: buildTimelineLegendItems(timelineSegments),
  }
}))
const legendOwnerKey = computed(() => (
  attemptRows.value.find((attempt) => attempt.timelineSegments.length > 0)?.key || ''
))

function attemptKey(attempt: ImageAttempt): string {
  return `${attempt.slot}-${attempt.attempt}-${attempt.accountEmail}`
}

function attemptTone(attempt: ImageAttempt): 'success' | 'danger' {
  return attempt.status === 'success' ? 'success' : 'danger'
}

function attemptStatusLabel(attempt: ImageAttempt): string {
  if (attempt.status !== 'success') return '失败'
  return attempt.failureCode ? '生成成功' : '成功'
}

function isAccountSwitch(index: number): boolean {
  if (index <= 0) return false
  return attemptRows.value[index - 1]?.slot === attemptRows.value[index]?.slot
}

function isRailLast(index: number): boolean {
  const current = attemptRows.value[index]
  const next = attemptRows.value[index + 1]
  return !next || current?.slot !== next.slot
}

function isAttemptDetailsVisible(key: string): boolean {
  return visibleAttemptDetails.value.includes(key)
}

function toggleAttemptDetails(key: string): void {
  visibleAttemptDetails.value = isAttemptDetailsVisible(key)
    ? visibleAttemptDetails.value.filter((item) => item !== key)
    : [...visibleAttemptDetails.value, key]
}

</script>

<style scoped>
.attempt-timeline {
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
  background: hsl(var(--card));
  overflow: hidden;
}

.attempt-timeline__header,
.attempt-timeline__row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.attempt-timeline__header {
  width: 100%;
  border: 0;
  background: transparent;
  padding: 12px 14px;
  text-align: left;
  color: inherit;
}

.attempt-timeline__header:hover {
  background: hsl(var(--muted) / 0.22);
}

.attempt-timeline__heading {
  min-width: 0;
}

.attempt-timeline__title {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  font-weight: 650;
  color: hsl(var(--foreground));
}

.attempt-timeline__title svg {
  width: 14px;
  height: 14px;
  color: rgb(180 83 9);
}

.attempt-timeline__header p {
  margin-top: 3px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.attempt-timeline__summary,
.attempt-timeline__facts,
.attempt-timeline__result {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.attempt-timeline__summary {
  flex: 0 0 auto;
  justify-content: flex-end;
}

.attempt-timeline__toggle-label {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  margin-left: 2px;
  font-size: 12px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}

.attempt-timeline__toggle-label svg {
  width: 14px;
  height: 14px;
  transition: transform 160ms ease;
}

.attempt-timeline__chevron--open {
  transform: rotate(180deg);
}

.attempt-timeline__list {
  border-top: 1px solid hsl(var(--border));
  padding: 4px 14px 2px;
}

.attempt-timeline__item {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  gap: 10px;
}

.attempt-timeline__rail {
  position: relative;
  display: flex;
  justify-content: center;
  padding-top: 12px;
}

.attempt-timeline__rail::after {
  position: absolute;
  top: 32px;
  bottom: -1px;
  width: 1px;
  background: hsl(var(--border));
  content: '';
}

.attempt-timeline__rail--last::after {
  display: none;
}

.attempt-timeline__rail span {
  z-index: 1;
  display: grid;
  width: 20px;
  height: 20px;
  place-items: center;
  border-radius: 999px;
  color: white;
}

.attempt-timeline__rail svg {
  width: 12px;
  height: 12px;
}

.attempt-timeline__marker--success {
  background: rgb(22 163 74);
}

.attempt-timeline__marker--danger {
  background: rgb(225 29 72);
}

.attempt-timeline__content {
  min-width: 0;
  padding: 12px 0 14px;
}

.attempt-timeline__item + .attempt-timeline__item .attempt-timeline__content {
  border-top: 1px solid hsl(var(--border) / 0.7);
}

.attempt-timeline__switch {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 8px;
  font-size: 11px;
  font-weight: 600;
  color: rgb(180 83 9);
}

.attempt-timeline__switch svg {
  width: 13px;
  height: 13px;
}

.attempt-timeline__identity {
  min-width: 0;
}

.attempt-timeline__identity strong,
.attempt-timeline__identity span {
  display: block;
  overflow-wrap: anywhere;
}

.attempt-timeline__identity strong {
  font-size: 12px;
  font-weight: 650;
  color: hsl(var(--foreground));
}

.attempt-timeline__identity span {
  margin-top: 3px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

.attempt-timeline__result {
  flex: 0 0 auto;
  justify-content: flex-end;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

.attempt-timeline__facts {
  margin-top: 8px;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

.attempt-timeline__fact {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
  line-height: 1.45;
}

.attempt-timeline__fact-label {
  color: hsl(var(--muted-foreground) / 0.72);
}

.attempt-timeline__facts code {
  overflow-wrap: anywhere;
  color: rgb(190 18 60);
}

.attempt-timeline__breakdown {
  margin-top: 11px;
  border-top: 1px dashed hsl(var(--border));
  padding-top: 10px;
}

@media (max-width: 640px) {
  .attempt-timeline__header {
    flex-direction: column;
  }

  .attempt-timeline__summary {
    justify-content: flex-start;
  }

  .attempt-timeline__row {
    gap: 8px;
  }

  .attempt-timeline__result {
    flex-direction: column-reverse;
    align-items: flex-end;
  }

}
</style>
