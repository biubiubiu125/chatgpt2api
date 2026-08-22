<template>
  <div class="space-y-6">
    <PagePanel class="space-y-5">
      <PanelHeader title="集群治理" align="start">
        <template #copy>
          <p class="mt-1 text-xs text-muted-foreground">
            主节点统一查看 Worker 在线状态、WireGuard IP、图片返回 URL、并发容量、账号和注册补号状态。
          </p>
          <p class="mt-1 text-xs text-muted-foreground">
            最近更新：{{ clusterState?.updated_at || '未获取' }}
          </p>
        </template>
        <template #actions>
          <StateBadge :tone="clusterHealthy ? 'success' : 'warning'" shape="rounded" size="sm">
            {{ clusterHealthy ? '运行中' : '待检查' }}
          </StateBadge>
          <Button size="sm" variant="outline" :disabled="loading" @click="loadClusterState(false)">
            {{ loading ? '刷新中...' : '立即刷新' }}
          </Button>
        </template>
      </PanelHeader>

      <PageLoadingState
        v-if="loading && !clusterState"
        title="正在加载集群状态"
        description="读取 PostgreSQL 队列、Worker 心跳、账号池和注册补号状态。"
      />

      <StateBlock
        v-else-if="error"
        compact
        dashed
        title="集群状态加载失败"
        :description="error"
      />

      <template v-else-if="clusterState">
        <StateBlock
          v-if="clusterState.join_error"
          compact
          dashed
          title="Join 状态读取失败"
          :description="clusterState.join_error"
        />
        <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <div class="cluster-card">
            <p class="cluster-card__label">当前节点</p>
            <p class="cluster-card__value">{{ clusterState.node.node_role || '-' }}</p>
            <p class="cluster-card__meta">
              API {{ clusterState.node.run_api ? '开启' : '关闭' }} / Worker {{ clusterState.node.run_worker ? '开启' : '关闭' }}
            </p>
          </div>
          <div class="cluster-card">
            <p class="cluster-card__label">在线 Worker</p>
            <p class="cluster-card__value">{{ onlineWorkerCount }} / {{ workerRows.length }}</p>
            <p class="cluster-card__meta">心跳阈值 180 秒</p>
          </div>
          <div class="cluster-card">
            <p class="cluster-card__label">剩余容量</p>
            <p class="cluster-card__value">{{ totalRemainingCapacity }}</p>
            <p class="cluster-card__meta">当前并发 {{ totalCurrentConcurrency }} / 有效容量 {{ totalEffectiveConcurrency }}</p>
          </div>
          <div class="cluster-card">
            <p class="cluster-card__label">可用账号 / Quota</p>
            <p class="cluster-card__value">{{ accountAvailable }} / {{ accountQuota }}</p>
            <p class="cluster-card__meta">未知额度 {{ accountUnknownQuota }}，无限额度 {{ accountUnlimitedQuota }}</p>
          </div>
        </div>

        <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <div class="cluster-card">
            <p class="cluster-card__label">队列任务</p>
            <p class="cluster-card__value">{{ queuedTasks }}</p>
            <p class="cluster-card__meta">运行 {{ runningJobs }}，重试 {{ retryJobs }}</p>
          </div>
          <div class="cluster-card">
            <p class="cluster-card__label">完成 / 失败</p>
            <p class="cluster-card__value">{{ successTasks }} / {{ failedTasks }}</p>
            <p class="cluster-card__meta">未 ACK 成功 {{ unacknowledgedSuccess }}</p>
          </div>
          <div class="cluster-card">
            <p class="cluster-card__label">注册补号</p>
            <p class="cluster-card__value">{{ registerStateText }}</p>
            <p class="cluster-card__meta">{{ registerPauseReason || '生图优先，资源足够时自动补号' }}</p>
          </div>
          <div class="cluster-card">
            <p class="cluster-card__label">当前图片 URL</p>
            <p class="cluster-card__value cluster-card__url">{{ workerImageUrlSummary }}</p>
            <p class="cluster-card__meta">从节点生成后返回本节点 URL</p>
            <p class="cluster-card__meta" :class="deliveryIssueWorkerCount ? 'text-red-600' : deliveryUnknownWorkerCount ? 'text-amber-600' : 'text-emerald-600'">
              {{ deliverySummaryText }}
            </p>
          </div>
        </div>
      </template>
    </PagePanel>

    <PagePanel flush>
      <div class="p-4">
        <PanelHeader title="Worker 列表" align="start">
          <template #copy>
            <p class="mt-1 text-xs text-muted-foreground">
              Worker 通过 PostgreSQL 自抢任务；图片保存在生成节点本机，主节点只返回 URL。
            </p>
          </template>
          <template #actions>
            <MetaChip size="xs" tone="muted">总数 {{ workerRows.length }}</MetaChip>
          </template>
        </PanelHeader>
      </div>
      <TableShell v-if="workerRows.length">
        <table class="cluster-table">
          <thead>
            <tr>
              <th>Worker</th>
              <th>状态</th>
              <th>WireGuard IP</th>
              <th>图片返回 URL</th>
              <th>图片交付</th>
              <th>并发 / 容量</th>
              <th>账号 / Quota</th>
              <th>最近心跳</th>
              <th>最近错误</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="worker in workerRows" :key="worker.worker_id">
              <td>
                <p class="font-medium text-foreground">{{ worker.worker_id || '-' }}</p>
                <p class="mt-1 text-[11px] text-muted-foreground">{{ worker.node_role || 'worker' }}</p>
              </td>
              <td>
                <StateBadge :tone="worker.online ? 'success' : 'warning'" shape="rounded" size="sm">
                  {{ worker.online ? '在线' : '离线' }}
                </StateBadge>
                <p v-if="worker.join_status" class="mt-1 text-[11px] text-muted-foreground">
                  Join: {{ worker.join_status }}
                </p>
                <p v-if="worker.pause_reason" class="mt-1 text-[11px] text-amber-600">{{ worker.pause_reason }}</p>
              </td>
              <td class="font-mono text-xs">{{ worker.wireguard_ip || '-' }}</td>
              <td class="max-w-[18rem] break-all text-xs">{{ worker.image_base_url || '-' }}</td>
              <td class="max-w-[18rem]">
                <StateBadge
                  :tone="worker.delivery_status === 'healthy' ? 'success' : worker.delivery_status === 'unhealthy' ? 'danger' : 'warning'"
                  shape="rounded"
                  size="sm"
                >
                  {{ worker.delivery_status === 'healthy' ? 'URL 可访问' : worker.delivery_status === 'unhealthy' ? 'URL 有问题' : '尚未检查' }}
                </StateBadge>
                <p v-if="worker.delivery_failures" class="mt-1 text-[11px] text-red-600">
                  最近失败 {{ worker.delivery_failures }} 次
                </p>
                <p v-if="worker.delivery_checked_at" class="mt-1 break-all text-[11px] text-muted-foreground">
                  检查于 {{ worker.delivery_checked_at }}
                </p>
                <p v-if="worker.delivery_error" class="mt-1 break-all text-[11px] text-red-600">
                  {{ worker.delivery_error }}
                </p>
              </td>
              <td>
                <p class="tabular-nums">{{ worker.current_concurrency }} / {{ worker.effective_concurrency }}</p>
                <p class="mt-1 text-[11px] text-muted-foreground">剩余 {{ worker.remaining_capacity }}</p>
              </td>
              <td>
                <p class="tabular-nums">{{ worker.available_account_count }} / {{ worker.available_quota }}</p>
                <p class="mt-1 text-[11px] text-muted-foreground">可用账号 / quota</p>
              </td>
              <td>
                <p class="text-xs">{{ worker.heartbeat_at || '-' }}</p>
                <p class="mt-1 text-[11px] text-muted-foreground">{{ formatSeconds(worker.heartbeat_age_seconds) }} 前</p>
              </td>
              <td class="max-w-[18rem] break-all text-xs text-muted-foreground">
                {{ worker.recent_error || '-' }}
              </td>
            </tr>
          </tbody>
        </table>
      </TableShell>
      <div v-else class="px-4 pb-4">
        <StateBlock compact dashed title="暂无 Worker 心跳" description="从节点启动并连上队列库后，会在这里显示心跳和容量。" />
      </div>
    </PagePanel>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Button } from 'nanocat-ui'
import { clusterApi, type ClusterState } from '@/api/cluster'
import MetaChip from '@/components/ai/MetaChip.vue'
import PageLoadingState from '@/components/ai/PageLoadingState.vue'
import PagePanel from '@/components/ai/PagePanel.vue'
import PanelHeader from '@/components/ai/PanelHeader.vue'
import StateBadge from '@/components/ai/StateBadge.vue'
import StateBlock from '@/components/ai/StateBlock.vue'
import TableShell from '@/components/ai/TableShell.vue'
import { errorMessage } from '@/lib/errorMessage'

defineOptions({ name: 'Cluster' })

const clusterState = ref<ClusterState | null>(null)
const loading = ref(false)
const error = ref('')
let timer: number | null = null

const workerRows = computed(() => clusterState.value?.workers || [])
const onlineWorkerCount = computed(() => workerRows.value.filter((item) => item.online).length)
const totalCurrentConcurrency = computed(() => sumWorkers('current_concurrency'))
const totalEffectiveConcurrency = computed(() => sumWorkers('effective_concurrency'))
const totalRemainingCapacity = computed(() => sumWorkers('remaining_capacity'))
const deliveryIssueWorkerCount = computed(() => workerRows.value.filter((item) => item.delivery_status === 'unhealthy').length)
const deliveryUnknownWorkerCount = computed(() => workerRows.value.filter((item) => item.online && (!item.delivery_status || item.delivery_status === 'unknown')).length)
const runtimeHealth = computed(() => (clusterState.value?.runtime_health || {}) as Record<string, any>)
const clusterHealthy = computed(() => Boolean(
  clusterState.value
  && (!clusterState.value.node.run_api || onlineWorkerCount.value > 0)
  && (runtimeHealth.value.healthy !== false)
  && deliveryIssueWorkerCount.value === 0,
))
const accounts = computed(() => clusterState.value?.accounts || {})
const queue = computed(() => clusterState.value?.queue || {})
const jobs = computed(() => (queue.value.jobs || {}) as Record<string, number>)
const tasks = computed(() => (queue.value.tasks || {}) as Record<string, number>)
const register = computed(() => clusterState.value?.register || {})
const registerStats = computed(() => (register.value.stats || {}) as Record<string, unknown>)
const accountAvailable = computed(() => Number(accounts.value.active || 0))
const accountQuota = computed(() => Number(accounts.value.total_quota || 0))
const accountUnknownQuota = computed(() => Number(accounts.value.unknown_quota_count || 0))
const accountUnlimitedQuota = computed(() => Number(accounts.value.unlimited_quota_count || 0))
const queuedTasks = computed(() => Number(tasks.value.queued || queue.value.queued || 0))
const runningJobs = computed(() => Number(jobs.value.running || 0) + Number(jobs.value.leased || 0))
const retryJobs = computed(() => Number(jobs.value.retry_wait || 0))
const successTasks = computed(() => Number(tasks.value.success || queue.value.success || 0))
const failedTasks = computed(() => Number(tasks.value.failed || queue.value.failed || 0))
const unacknowledgedSuccess = computed(() => Number(queue.value.unacknowledged_success || 0))
const registerPauseReason = computed(() => String(registerStats.value.pause_reason || ''))
const workerImageUrlSummary = computed(() => {
  if (!workerRows.value.length) return '-'
  if (deliveryIssueWorkerCount.value) return `异常 ${deliveryIssueWorkerCount.value} 个`
  if (deliveryUnknownWorkerCount.value) return `待检查 ${deliveryUnknownWorkerCount.value} 个`
  return `正常 ${workerRows.value.length} 个`
})
const deliverySummaryText = computed(() => {
  if (deliveryIssueWorkerCount.value) return `URL 异常 Worker ${deliveryIssueWorkerCount.value} 个`
  if (deliveryUnknownWorkerCount.value) return `URL 待检查 Worker ${deliveryUnknownWorkerCount.value} 个`
  return '当前没有已发现的图片 URL 异常'
})
const registerStateText = computed(() => {
  const state = String(register.value.state || 'idle')
  const map: Record<string, string> = {
    idle: '空闲',
    running: '运行中',
    stopping: '停止中',
    paused: '已暂停',
  }
  return map[state] || state
})

function sumWorkers(key: 'current_concurrency' | 'effective_concurrency' | 'remaining_capacity') {
  return workerRows.value.reduce((total, item) => total + Number(item[key] || 0), 0)
}

function formatSeconds(value: number) {
  const seconds = Math.max(0, Math.round(Number(value || 0)))
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  return `${Math.floor(minutes / 60)}h`
}

async function loadClusterState(silent = true) {
  if (loading.value && silent) return
  loading.value = true
  try {
    clusterState.value = await clusterApi.state()
    error.value = ''
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

function startPolling() {
  stopPolling()
  timer = window.setInterval(() => {
    void loadClusterState(true)
  }, 5000)
}

function stopPolling() {
  if (timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
}

onMounted(() => {
  void loadClusterState(false)
  startPolling()
})

onBeforeUnmount(() => {
  stopPolling()
})
</script>

<style scoped>
.cluster-card {
  min-width: 0;
  border-radius: 16px;
  border: 1px solid hsl(var(--border));
  background: hsl(var(--background));
  padding: 14px;
}

.cluster-card__label {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
}

.cluster-card__value {
  margin-top: 6px;
  color: hsl(var(--foreground));
  font-size: 22px;
  font-weight: 700;
  line-height: 1.15;
}

.cluster-card__url {
  overflow-wrap: anywhere;
  font-size: 13px;
}

.cluster-card__meta {
  margin-top: 6px;
  color: hsl(var(--muted-foreground));
  font-size: 11px;
}

.cluster-table {
  width: 100%;
  min-width: 1140px;
  border-collapse: collapse;
  text-align: left;
  font-size: 13px;
}

.cluster-table th {
  border-bottom: 1px solid hsl(var(--border));
  background: hsl(var(--muted) / 0.42);
  padding: 10px 14px;
  color: hsl(var(--muted-foreground));
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.cluster-table td {
  border-bottom: 1px solid hsl(var(--border) / 0.72);
  padding: 12px 14px;
  vertical-align: top;
  color: hsl(var(--foreground));
  line-height: 1.45;
}

.cluster-table tbody tr:hover td {
  background: hsl(var(--muted) / 0.28);
}
</style>
