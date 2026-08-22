<template>
  <div class="min-h-screen">
    <div class="flex min-h-screen flex-col lg:flex-row">
      <div
        v-if="isSidebarOpen"
        class="fixed inset-0 z-30 bg-black/20 lg:hidden"
        @click="isSidebarOpen = false"
      ></div>
      <aside
        class="fixed inset-y-0 left-0 z-40 w-64 -translate-x-full overflow-x-hidden bg-card border-r border-border
               transition-[transform,width] duration-200 ease-out will-change-[transform,width] transform-gpu flex flex-col lg:static lg:translate-x-0 lg:w-[var(--sidebar-width)] lg:bg-card
               lg:border-b-0 lg:border-r lg:sticky lg:top-0 lg:h-screen"
        :class="[isSidebarOpen ? 'translate-x-0' : '']"
        :style="sidebarStyle"
      >
        <div
          class="flex h-16 items-center pt-4 lg:h-20 lg:pt-5"
          :class="isSidebarRail ? 'justify-center px-2' : 'justify-between px-6'"
        >
          <div class="flex items-center gap-2" :class="isSidebarRail ? 'gap-0 justify-center w-full' : ''">
            <a
              href="https://github.com/biubiubiu125/chatgpt2api"
              target="_blank"
              rel="noopener noreferrer"
              class="text-foreground transition-colors hover:text-primary"
              aria-label="GitHub"
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                class="h-6 w-6"
                fill="currentColor"
              >
                <path d="M12 2C6.477 2 2 6.477 2 12c0 4.419 2.865 8.166 6.839 9.489.5.09.682-.217.682-.483 0-.237-.009-.868-.014-1.703-2.782.604-3.369-1.341-3.369-1.341-.454-1.154-1.11-1.462-1.11-1.462-.908-.62.069-.608.069-.608 1.004.071 1.532 1.031 1.532 1.031.892 1.529 2.341 1.087 2.91.832.091-.647.349-1.087.636-1.337-2.22-.253-4.555-1.11-4.555-4.944 0-1.092.39-1.987 1.029-2.687-.103-.253-.446-1.272.098-2.65 0 0 .84-.269 2.75 1.026A9.564 9.564 0 0 1 12 6.844c.85.004 1.705.115 2.504.337 1.909-1.295 2.748-1.026 2.748-1.026.546 1.378.202 2.397.1 2.65.64.7 1.028 1.595 1.028 2.687 0 3.842-2.338 4.687-4.566 4.936.359.309.678.919.678 1.852 0 1.337-.012 2.418-.012 2.747 0 .268.18.577.688.479A10.002 10.002 0 0 0 22 12c0-5.523-4.477-10-10-10z" />
              </svg>
            </a>
            <div v-if="!isSidebarRail" class="min-w-0">
              <p class="ui-section-title">ChatGPT2API</p>
            </div>
          </div>
        </div>

        <nav
          class="flex-1 pb-3 pt-3 lg:pt-4"
          :class="isSidebarRail ? 'px-2' : 'px-3'"
        >
          <p
            v-if="!isSidebarRail"
            class="px-3 pb-2 text-xs uppercase tracking-[0.28em] text-muted-foreground"
          >
            导航
          </p>
          <div class="space-y-1">
            <RouterLink
              v-for="item in visibleMenuItems"
              :key="item.path"
              :to="item.path"
              class="group flex items-center overflow-hidden rounded-lg border border-transparent py-1.5 text-sm font-medium transition-colors"
              :class="navItemClassMap[item.path]"
              :title="isSidebarRail ? item.label : undefined"
              @mouseenter="prefetchRouteView(item.path)"
              @focus="prefetchRouteView(item.path)"
              @click="handleNavClick"
            >
              <span
                class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border transition-colors"
                :class="navIconClassMap[item.path]"
              >
                <svg aria-hidden="true" viewBox="0 0 24 24" class="h-4 w-4" fill="currentColor">
                  <path :d="item.icon" />
                </svg>
              </span>
              <span v-if="!isSidebarRail" class="flex-1 min-w-0 truncate">{{ item.label }}</span>
            </RouterLink>
          </div>
          <div v-if="visibleUtilityMenuItems.length" class="mt-4 border-t border-border/70 pt-3">
            <p
              v-if="!isSidebarRail"
              class="px-3 pb-2 text-xs uppercase tracking-[0.28em] text-muted-foreground"
            >
              工具
            </p>
            <div class="space-y-1">
              <RouterLink
                v-for="item in visibleUtilityMenuItems"
                :key="item.path"
                :to="item.path"
                class="group flex items-center overflow-hidden rounded-lg border border-transparent py-1.5 text-sm font-medium transition-colors"
                :class="navItemClassMap[item.path]"
                :title="isSidebarRail ? item.label : undefined"
                @mouseenter="prefetchRouteView(item.path)"
                @focus="prefetchRouteView(item.path)"
              >
                <span
                  class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border transition-colors"
                  :class="navIconClassMap[item.path]"
                >
                  <svg aria-hidden="true" viewBox="0 0 24 24" class="h-4 w-4" fill="currentColor">
                    <path :d="item.icon" />
                  </svg>
                </span>
                <span v-if="!isSidebarRail" class="flex-1 min-w-0 truncate">{{ item.label }}</span>
              </RouterLink>
            </div>
          </div>
        </nav>

        <div class="mt-auto border-t border-border py-3" :class="isSidebarRail ? 'px-2' : 'px-6'">
          <div
            class="flex items-center gap-3"
            :class="isSidebarRail ? 'justify-center' : ''"
          >
            <Button
              v-if="!isSidebarRail"
              size="sm"
              variant="outline"
              block
              root-class="justify-center rounded-2xl text-muted-foreground"
              @click="handleLogout"
            >
              退出登录
            </Button>
            <Button
              v-if="!isImmersivePage"
              size="xs"
              variant="outline"
              icon-only
              root-class="shrink-0 rounded-2xl text-muted-foreground"
              @click="isSidebarCollapsed = !isSidebarCollapsed"
              :title="isSidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                class="h-4 w-4 shrink-0"
                fill="currentColor"
              >
                <path d="M6 4h2v16H6V4zm4 4h8v2h-8V8zm0 6h8v2h-8v-2z" />
              </svg>
            </Button>
          </div>
        </div>
      </aside>

      <main class="relative min-w-0 flex-1 overflow-hidden lg:ml-0">
        <div
          v-if="isRoutePending"
          class="route-pending-bar"
          role="status"
          :aria-label="routePendingText"
        ></div>

        <header
          v-if="!isImmersivePage"
          class="min-w-0 flex flex-col gap-4 border-b border-border bg-card px-6 py-5 lg:flex-row lg:items-center lg:justify-between lg:px-10"
        >
          <div class="flex items-center gap-3">
            <Button
              size="xs"
              variant="outline"
              icon-only
              root-class="lg:hidden"
              @click="isSidebarOpen = true"
              aria-label="打开导航"
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" class="h-5 w-5" fill="currentColor">
                <path d="M4 6h16v2H4V6zm0 5h16v2H4v-2zm0 5h16v2H4v-2z" />
              </svg>
            </Button>
            <svg
              aria-hidden="true"
              viewBox="0 0 130 150"
              class="logo-mark h-9 w-9 shrink-0 text-foreground"
            >
              <defs>
                <filter id="head-shadow" x="-50%" y="-50%" width="200%" height="200%">
                  <feDropShadow dx="0" dy="10" stdDeviation="12" flood-color="rgba(0, 188, 212, 0.2)"/>
                </filter>
              </defs>
              <g class="logo-cat-wrapper" transform="translate(0, 12)">
                <g transform="translate(16, 20) rotate(-10, 9, 12)">
                  <path d="M14 0 L18 24 L0 24 Z" fill="#2c3e50" />
                </g>
                <g transform="translate(96, 20) rotate(10, 9, 12)">
                  <path d="M4 0 L18 24 L0 24 Z" fill="#2c3e50" />
                </g>
                <g filter="url(#head-shadow)">
                  <path d="M 32 40 L 98 40 A 12 12 0 0 1 110 52 L 110 90 A 30 30 0 0 1 80 120 L 50 120 A 30 30 0 0 1 20 90 L 20 52 A 12 12 0 0 1 32 40 Z"
                    fill="rgba(255, 255, 255, 0.9)"
                    stroke="#2c3e50"
                    stroke-width="3"
                  />
                </g>
                <rect class="logo-eye" x="35" y="68" width="14" height="4" rx="1" />
                <rect class="logo-eye" x="81" y="68" width="14" height="4" rx="1" />
              </g>
            </svg>
            <div class="flex min-w-0 flex-wrap items-center gap-2">
              <h2 class="text-xl font-semibold text-foreground lg:text-2xl">
                {{ currentPageTitle }}
              </h2>
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-3">
            <Button
              size="sm"
              variant="outline"
              @click="cycleThemeMode"
              :title="themeButtonTitle"
            >
              {{ themeButtonText }}
            </Button>
            <Button
              v-if="canvasHref"
              size="sm"
              variant="outline"
              @click="openInfiniteCanvas"
              title="打开外部无限画布"
            >
              画布
            </Button>
            <Button
              size="sm"
              variant="outline"
              @click="refreshPage"
              title="刷新"
            >
              刷新
            </Button>
            <Button
              size="sm"
              variant="outline"
              v-if="authStore.isAdmin"
              @click="openUpdateDialog"
              title="查看版本更新"
            >
              {{ versionButtonText }}
            </Button>
            <Button
              size="sm"
              variant="outline"
              v-if="authStore.isAdmin"
              @click="openApiInfo"
            >
              接口信息
            </Button>
          </div>
        </header>

        <div
          class="relative h-full overflow-y-auto overflow-x-hidden bg-card"
          :class="isImmersivePage ? 'p-0' : 'px-4 pb-10 pt-6 lg:px-10 lg:pt-10'"
        >
          <RouterView v-slot="{ Component, route: currentRoute }">
            <Suspense :timeout="120">
              <template #default>
                <div class="route-view-content" :class="{ 'h-full': isImmersivePage }">
                  <KeepAlive :include="cachedRouteNames" :max="cachedRouteMax">
                    <component
                      :is="Component"
                      :key="String(currentRoute.name || currentRoute.path)"
                    />
                  </KeepAlive>
                </div>
              </template>
              <template #fallback>
                <PageLoadingState
                  :title="routePendingText"
                  description="正在准备页面内容..."
                  compact
                  dashed
                />
              </template>
            </Suspense>
          </RouterView>
        </div>
      </main>
    </div>
    <ConfirmDialog
      :open="confirmDialog.open.value"
      :title="confirmDialog.title.value"
      :message="confirmDialog.message.value"
      :confirm-text="confirmDialog.confirmText.value"
      :cancel-text="confirmDialog.cancelText.value"
      @confirm="confirmDialog.confirm"
      @cancel="confirmDialog.cancel"
    />
    <ModalShell
      :open="isApiInfoOpen"
      max-width="32rem"
      :z-index="100"
      panel-class="p-6"
      close-on-backdrop
      @close="isApiInfoOpen = false"
    >
          <ModalHeader
            title="API 接口"
            subtitle="根据客户端选择对应接口"
            title-class="ui-subsection-title"
            :bordered="false"
            flush
            @close="isApiInfoOpen = false"
          />

          <div class="mt-4 space-y-3 text-sm">
            <div>
              <p class="text-xs text-muted-foreground">基础端点</p>
              <div class="mt-1 flex items-start gap-2">
                <ValueSurface
                  tag="p"
                  mono
                  break-mode="all"
                  root-class="min-w-0 flex-1"
                >
                  {{ apiBaseUrl }}
                </ValueSurface>
                <Button
                  size="sm"
                  variant="outline"
                  root-class="shrink-0 text-[11px] text-muted-foreground"
                  @click="copyText(apiBaseUrl)"
                >
                  复制
                </Button>
              </div>
            </div>
            <div>
              <p class="text-xs text-muted-foreground">SDK 接口</p>
              <div class="mt-1 flex items-start gap-2">
                <ValueSurface
                  tag="p"
                  mono
                  break-mode="all"
                  root-class="min-w-0 flex-1"
                >
                  {{ apiSdkUrl }}
                </ValueSurface>
                <Button
                  size="sm"
                  variant="outline"
                  root-class="shrink-0 text-[11px] text-muted-foreground"
                  @click="copyText(apiSdkUrl)"
                >
                  复制
                </Button>
              </div>
            </div>
            <div>
              <p class="text-xs text-muted-foreground">完整接口</p>
              <div class="mt-1 flex items-start gap-2">
                <ValueSurface
                  tag="p"
                  mono
                  break-mode="all"
                  root-class="min-w-0 flex-1"
                >
                  {{ apiFullUrl }}
                </ValueSurface>
                <Button
                  size="sm"
                  variant="outline"
                  root-class="shrink-0 text-[11px] text-muted-foreground"
                  @click="copyText(apiFullUrl)"
                >
                  复制
                </Button>
              </div>
            </div>
            <div>
              <p class="text-xs text-muted-foreground">支持模型</p>
              <div class="mt-1 space-y-3 rounded-2xl border border-border bg-background px-3 py-2 text-xs text-muted-foreground">
                <div>
                  <p class="mb-1 text-[11px] text-muted-foreground">聊天模型</p>
                  <div class="flex flex-wrap gap-2 text-foreground">
                    <MetaChip
                      v-for="model in supportedChatModels"
                      :key="`chat-${model}`"
                      size="xs"
                    >
                      {{ model }}
                    </MetaChip>
                  </div>
                </div>
                <div>
                  <p class="mb-1 text-[11px] text-muted-foreground">图片模型</p>
                <div class="flex flex-wrap gap-2 text-foreground">
                  <MetaChip
                    v-for="model in supportedImageModels"
                    :key="`image-${model}`"
                    size="xs"
                  >
                    {{ model }}
                  </MetaChip>
                </div>
                </div>
              </div>
            </div>
            <div>
              <p class="text-xs text-muted-foreground">当前调用密钥</p>
              <div class="mt-1 flex items-start gap-2">
                <ValueSurface
                  tag="p"
                  mono
                  break-mode="all"
                  root-class="min-w-0 flex-1"
                >
                  {{ apiKeyDisplay }}
                </ValueSurface>
                <Button
                  size="sm"
                  variant="outline"
                  root-class="shrink-0 text-[11px] text-muted-foreground"
                  :disabled="!currentAuthToken"
                  @click="copyText(apiKeyDisplay)"
                >
                  复制
                </Button>
              </div>
              <p class="mt-1 text-[11px] text-muted-foreground">
                请求头使用 Authorization: Bearer &lt;当前调用密钥&gt;。
              </p>
            </div>
          </div>

          <ModalFooter class="mt-6" :bordered="false" flush>
            <Button
              size="xs"
              variant="primary"
              root-class="min-w-14 justify-center"
              @click="isApiInfoOpen = false"
            >
              知道了
            </Button>
          </ModalFooter>
    </ModalShell>
    <ModalShell
      :open="isUpdateDialogOpen"
      max-width="42rem"
      :z-index="100"
      panel-class="flex max-h-[min(46rem,calc(100dvh-2rem))] flex-col p-6"
      close-on-backdrop
      @close="isUpdateDialogOpen = false"
    >
      <ModalHeader
        title="版本更新"
        subtitle="查看当前版本和更新日志"
        title-class="ui-subsection-title"
        :bordered="false"
        flush
        @close="isUpdateDialogOpen = false"
      />

      <div class="mt-4 grid gap-3 sm:grid-cols-2">
        <div class="rounded-2xl border border-border bg-background px-4 py-3">
          <p class="text-xs text-muted-foreground">当前版本</p>
          <p class="mt-1 text-base font-semibold text-foreground">{{ currentVersionLabel }}</p>
        </div>
        <div class="rounded-2xl border border-border bg-background px-4 py-3">
          <div class="flex items-center justify-between gap-3">
            <p class="text-xs text-muted-foreground">最新版本</p>
            <button
              type="button"
              class="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline disabled:cursor-not-allowed disabled:opacity-60"
              :disabled="isCheckingUpdate"
              @click="checkForUpdates(true)"
            >
              {{ isCheckingUpdate ? '检查中...' : '检查更新' }}
            </button>
          </div>
          <p
            class="mt-1 text-base font-semibold"
            :class="hasNewVersion ? 'text-rose-600 dark:text-rose-400' : 'text-foreground'"
          >
            {{ latestVersionLabel }}
          </p>
        </div>
      </div>

      <div
        v-if="updateCheckMessage"
        class="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-2xl border px-4 py-3 text-sm"
        :class="updateCheckMessageClass"
      >
        <span class="min-w-0 flex-1 font-medium">{{ updateCheckMessage }}</span>
        <MetaChip
          size="xs"
          :tone="updateCheckBadgeTone"
          strong
          chip-class="shrink-0"
        >
          {{ updateCheckBadgeText }}
        </MetaChip>
      </div>

      <div class="scrollbar-slim mt-5 min-h-0 flex-1 space-y-5 overflow-y-auto pr-2">
        <div
          v-for="release in releaseEntries"
          :key="`${release.version}-${release.date}`"
          class="border-l border-border pl-4"
        >
          <div class="flex flex-wrap items-center gap-2">
            <span class="text-sm font-semibold text-foreground">
              {{ release.version === 'Unreleased' ? '未发布' : release.version }}
            </span>
            <span v-if="release.date" class="text-xs text-muted-foreground">{{ release.date }}</span>
            <MetaChip
              v-if="normalizeVersionTag(release.version) === latestVersionLabel"
              size="xs"
              tone="success"
              strong
            >
              最新
            </MetaChip>
            <MetaChip
              v-if="normalizeVersionTag(release.version) === currentVersionLabel"
              size="xs"
              tone="muted"
            >
              当前
            </MetaChip>
          </div>
          <div class="mt-2 space-y-1.5">
            <div
              v-for="(item, index) in release.items"
              :key="`${release.version}-${index}`"
              class="flex items-start gap-2 text-sm leading-6 text-muted-foreground"
            >
              <MetaChip
                size="xs"
                :tone="releaseItemTone(item.type)"
                strong
                chip-class="mt-0.5 shrink-0"
              >
                {{ item.type }}
              </MetaChip>
              <span class="min-w-0 flex-1 text-foreground/85">
                <template
                  v-for="(segment, segmentIndex) in splitReleaseInlineCode(item.content)"
                  :key="`${release.version}-${index}-${segmentIndex}`"
                >
                  <code
                    v-if="segment.kind === 'code'"
                    class="rounded bg-muted px-1 py-0.5 font-mono text-[0.9em] text-foreground"
                  >{{ segment.content }}</code>
                  <span v-else>{{ segment.content }}</span>
                </template>
              </span>
            </div>
          </div>
        </div>
        <div v-if="!releaseEntries.length" class="rounded-2xl border border-dashed border-border bg-muted/30 px-4 py-6 text-center text-sm text-muted-foreground">
          暂无更新日志。
        </div>
      </div>

      <ModalFooter class="mt-6" :bordered="false" flush>
        <Button
          size="xs"
          variant="outline"
          @click="openReleasePage"
        >
          打开发布页
        </Button>
        <Button
          size="xs"
          variant="primary"
          root-class="min-w-14 justify-center"
          @click="isUpdateDialogOpen = false"
        >
          知道了
        </Button>
      </ModalFooter>
    </ModalShell>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { settingsApi } from '@/api/settings'
import { versionApi } from '@/api/version'
import { getAuthToken } from '@/api/client'
import { useSettingsStore } from '@/stores/settings'
import { useAuthStore } from '@/stores/auth'
import { useModelCatalog } from '@/composables/useModelCatalog'
import { Button, ValueSurface } from 'nanocat-ui'
import ConfirmDialog from '@/components/ui/AppConfirmDialog.vue'
import MetaChip from '@/components/ai/MetaChip.vue'
import ModalFooter from '@/components/ai/ModalFooter.vue'
import ModalHeader from '@/components/ai/ModalHeader.vue'
import ModalShell from '@/components/ai/ModalShell.vue'
import PageLoadingState from '@/components/ai/PageLoadingState.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { useToast } from '@/composables/useToast'
import { getBooleanPreference, preferenceKeys, setBooleanPreference } from '@/lib/preferences'
import { applyThemeMode, getStoredThemeMode, setStoredThemeMode, type ThemeMode } from '@/lib/theme'
import { buildThirdPartyHref } from '@/utils/thirdPartyLinks'
import {
  isNewerVersion,
  latestReleasedVersion,
  normalizeVersionTag,
  parseChangelog,
  splitReleaseInlineCode,
  type ReleaseInfo,
} from '@/lib/release'
import type { Settings } from '@/types/api'
import localVersion from '../../../VERSION?raw'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const settingsStore = useSettingsStore()
const toast = useToast()
const isSidebarOpen = ref(false)
const isSidebarCollapsed = ref(false)
const confirmDialog = useConfirmDialog()
const isApiInfoOpen = ref(false)
const isUpdateDialogOpen = ref(false)
const isCheckingUpdate = ref(false)
const currentVersionTag = ref(normalizeVersionTag(localVersion))
const latestVersionTag = ref('')
const releaseEntries = ref<ReleaseInfo[]>([])
const releaseEntriesSource = ref<'none' | 'local' | 'remote'>('none')
const updateCheckMessage = ref('')
const updateCheckStatus = ref<'idle' | 'checking' | 'available' | 'current' | 'warning'>('idle')
const currentAuthToken = ref('')
const thirdPartyApps = ref<Settings['third_party_apps'] | null>(null)
const themeMode = ref<ThemeMode>(getStoredThemeMode())
const isRoutePending = ref(false)
const pendingRouteTitle = ref('')
const cachedRouteNames = ['Studio', 'Accounts', 'Logs', 'Monitor', 'Cluster', 'Gallery', 'Proxy', 'Register', 'Settings']
const cachedRouteMax = cachedRouteNames.length
const themeOptions: { label: string; value: ThemeMode }[] = [
  { label: '浅色', value: 'light' },
  { label: '深色', value: 'dark' },
  { label: '系统', value: 'system' },
]
const {
  chatModels: supportedChatModels,
  imageModels: supportedImageModels,
  loadModelCatalog,
} = useModelCatalog(() => settingsStore.settings)

const menuItems = [
  {
    path: '/',
    label: '概览中心',
    icon: 'M4 4h7v7H4V4zm9 0h7v4h-7V4zm0 6h7v10h-7V10zM4 13h7v7H4v-7z',
  },
  {
    path: '/monitor',
    label: '实时监控',
    icon: 'M4 5h3v14H4V5zm5 6h3v8H9v-8zm5-4h3v12h-3V7zm5 7h3v5h-3v-5z',
  },
  {
    path: '/cluster',
    label: '集群治理',
    icon: 'M4 5h6v6H4V5zm10 0h6v6h-6V5zM4 13h6v6H4v-6zm10 0h6v6h-6v-6zM10 8h4v2h-4V8zm2 2h2v3h-2v-3z',
  },
  {
    path: '/studio',
    label: '对话画图',
    icon: 'M5 4h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-5l-4 4v-4H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zm1 3v6h12V7H6zm2 2 2.1 2.8 2.4-3.1L17 14H7l1-5z',
  },
  {
    path: '/accounts',
    label: '账号管理',
    icon: 'M12 12a3.5 3.5 0 1 0-3.5-3.5A3.5 3.5 0 0 0 12 12zm0 2c-4.1 0-7.5 2.2-7.5 5v1h15v-1c0-2.8-3.4-5-7.5-5z',
  },
  {
    path: '/register',
    label: '注册账号',
    icon: 'M7 3h10a2 2 0 0 1 2 2v3h-2V5H7v14h10v-3h2v3a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zm8.6 5.4L20.2 13l-4.6 4.6-1.4-1.4 2.2-2.2H9v-2h7.4l-2.2-2.2 1.4-1.4z',
  },
  {
    path: '/logs',
    label: '日志管理',
    icon: 'M4 6h16v2H4V6zm0 5h16v2H4v-2zm0 5h10v2H4v-2z',
  },
  {
    path: '/gallery',
    label: '图片管理',
    icon: 'M22 16V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2zm-11-4 2.03 2.71L16 11l4 5H8l3-3zM2 6v14a2 2 0 0 0 2 2h14v-2H4V6H2z',
  },
  {
    path: '/proxy',
    label: '代理管理',
    icon: 'M12 3a5 5 0 0 1 5 5v2h1a3 3 0 0 1 3 3v5a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3v-5a3 3 0 0 1 3-3h1V8a5 5 0 0 1 5-5zm-3 7h6V8a3 3 0 0 0-6 0v2zm-3 2a1 1 0 0 0-1 1v5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5a1 1 0 0 0-1-1H6z',
  },
  {
    path: '/settings',
    label: '系统设置',
    icon: 'M4 6h10v2H4V6zm12 0h4v2h-4V6zM4 11h6v2H4v-2zm8 0h8v2h-8v-2zM4 16h10v2H4v-2zm12 0h4v2h-4v-2z',
  },
]

const utilityMenuItems = [
  {
    path: '/debug',
    label: '调试中心',
    icon: 'M5 4h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-5l-4 4v-4H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zm1 3v2h8V7H6zm0 4v2h5v-2H6zm11-4h-2v2h2V7zm0 4h-2v2h2v-2z',
  },
]

const routeTitleMap: Record<string, string> = {
  dashboard: '概览中心',
  accounts: '账号管理',
  logs: '日志管理',
  gallery: '图片管理',
  proxy: '代理管理',
  register: '注册账号',
  settings: '系统设置',
  debug: '调试中心',
  monitor: '实时监控',
  cluster: '集群治理',
  docs: '文档教程',
  studio: '对话画图',
}

const visibleMenuItems = computed(() => {
  if (authStore.isUser) {
    return menuItems.filter(item => item.path === '/studio')
  }
  return menuItems
})

const visibleUtilityMenuItems = computed(() => (authStore.isAdmin ? utilityMenuItems : []))

const currentPageTitle = computed(() => {
  const routeName = String(route.name || '')
  const item = [...visibleMenuItems.value, ...visibleUtilityMenuItems.value].find(item => isNavActive(item.path))
  return item?.label || routeTitleMap[routeName] || '概览中心'
})

function titleForRoute(name: unknown, path: string) {
  const routeName = String(name || '')
  const item = [...menuItems, ...utilityMenuItems].find((menuItem) => menuItem.path === path)
  return item?.label || routeTitleMap[routeName] || '页面'
}

const isImmersivePage = computed(() => Boolean(route.meta.immersive))
const isSidebarRail = computed(() => isSidebarCollapsed.value || isImmersivePage.value)
const sidebarStyle = computed(() => ({
  '--sidebar-width': isSidebarRail.value ? '4rem' : '16rem',
}))

const navItemBaseClass = computed(() => isSidebarRail.value ? 'px-2 justify-center gap-0' : 'px-2.5 gap-3')
const activeNavPathSet = computed(() => {
  const name = String(route.name || '')
  const currentPath = route.path
  return new Set(
    [...visibleMenuItems.value, ...visibleUtilityMenuItems.value]
      .filter((item) => isRoutePathActive(item.path, name, currentPath))
      .map((item) => item.path),
  )
})

function isRoutePathActive(path: string, name: string, currentPath: string) {
  const normalized = path.replace(/^\/+/, '')
  if (!normalized) return name === 'dashboard' || currentPath === '/'
  return currentPath === path || name === normalized
}

const isNavActive = (path: string) => {
  return activeNavPathSet.value.has(path)
}

function buildNavItemClass(path: string) {
  const base = navItemBaseClass.value
  if (isNavActive(path)) {
    return `${base} rounded-[0.9rem] border-[hsl(var(--primary)_/_0.28)] bg-[hsl(var(--primary)_/_0.08)] font-semibold text-foreground shadow-[inset_0_0_0_1px_hsl(var(--primary)_/_0.08)]`
  }
  return `${base} rounded-[0.9rem] border-transparent text-muted-foreground hover:border-border hover:bg-[hsl(var(--secondary)_/_0.55)] hover:text-foreground`
}

function buildNavIconClass(path: string) {
  if (isNavActive(path)) {
    return 'border-[hsl(var(--primary)_/_0.28)] bg-[hsl(var(--card))] text-foreground shadow-sm'
  }
  return 'border-border bg-[hsl(var(--card))] text-muted-foreground group-hover:border-[hsl(var(--foreground)_/_0.28)] group-hover:text-foreground'
}

const navItemClassMap = computed<Record<string, string>>(() => {
  const entries = [...visibleMenuItems.value, ...visibleUtilityMenuItems.value].map((item) => [item.path, buildNavItemClass(item.path)])
  return Object.fromEntries(entries)
})

const navIconClassMap = computed<Record<string, string>>(() => {
  const entries = [...visibleMenuItems.value, ...visibleUtilityMenuItems.value].map((item) => [item.path, buildNavIconClass(item.path)])
  return Object.fromEntries(entries)
})


const apiBaseUrl = computed(() => {
  const raw = settingsStore.settings?.basic?.base_url
    || import.meta.env.VITE_API_URL
    || window.location.origin
  return raw.replace(/\/$/, '')
})

const apiSdkUrl = computed(() => `${apiBaseUrl.value}/v1`)
const apiFullUrl = computed(() => `${apiBaseUrl.value}/v1/chat/completions`)
const apiKeyDisplay = computed(() => currentAuthToken.value || '未登录')
const currentVersionLabel = computed(() => normalizeVersionTag(currentVersionTag.value || ''))
const latestVersionLabel = computed(() => normalizeVersionTag(
  latestVersionTag.value || latestReleasedVersion(releaseEntries.value) || currentVersionTag.value || '',
))
const versionButtonText = computed(() => currentVersionLabel.value || '版本')
const hasNewVersion = computed(() => isNewerVersion(latestVersionLabel.value, currentVersionLabel.value))
const updateCheckMessageClass = computed(() => {
  if (updateCheckStatus.value === 'available') return 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700'
  if (updateCheckStatus.value === 'checking') return 'border-cyan-500/35 bg-cyan-500/10 text-cyan-700'
  if (updateCheckStatus.value === 'warning') return 'border-amber-500/40 bg-amber-500/10 text-amber-700'
  return 'border-border bg-muted/40 text-muted-foreground'
})
const updateCheckBadgeText = computed(() => {
  if (updateCheckStatus.value === 'available') return '可更新'
  if (updateCheckStatus.value === 'checking') return '检查中'
  if (updateCheckStatus.value === 'warning') return '部分失败'
  return '已是最新'
})
const updateCheckBadgeTone = computed(() => {
  if (updateCheckStatus.value === 'available') return 'success'
  if (updateCheckStatus.value === 'checking') return 'info'
  if (updateCheckStatus.value === 'warning') return 'warning'
  return 'muted'
})
function releaseItemTone(type: string): 'default' | 'muted' | 'success' | 'warning' | 'danger' | 'info' {
  const value = String(type || '').trim()
  if (['新增', '添加', 'Added'].includes(value)) return 'success'
  if (['优化', '改进', 'Changed', 'Improved'].includes(value)) return 'info'
  if (['修复', '修正', 'Fixed'].includes(value)) return 'warning'
  if (['移除', '删除', '废弃', 'Removed', 'Deprecated'].includes(value)) return 'danger'
  return 'muted'
}
const activeThirdPartyApps = computed(() => settingsStore.settings?.third_party_apps || thirdPartyApps.value)
const canvasHref = computed(() => {
  const canvas = activeThirdPartyApps.value?.infinite_canvas
  if (!canvas?.enabled || !canvas.url.trim()) return ''
  return buildThirdPartyHref(canvas.url, apiSdkUrl.value)
})
const themeButtonText = computed(() => themeOptions.find(option => option.value === themeMode.value)?.label || '系统')
const themeButtonTitle = computed(() => `当前主题：${themeButtonText.value}，点击切换`)
const routePendingText = computed(() => `正在打开${pendingRouteTitle.value || currentPageTitle.value}`)
let systemThemeMedia: MediaQueryList | null = null
let routePendingTimer: number | null = null
let stopRoutePendingBeforeEach: (() => void) | null = null
let stopRoutePendingAfterEach: (() => void) | null = null
let stopRoutePendingError: (() => void) | null = null
const prefetchedRoutePaths = new Set<string>()
const releasePageUrl = 'https://github.com/biubiubiu125/chatgpt2api/releases'
const latestVersionUrl = 'https://raw.githubusercontent.com/biubiubiu125/chatgpt2api/main/VERSION'
const latestChangelogUrl = 'https://raw.githubusercontent.com/biubiubiu125/chatgpt2api/main/CHANGELOG.md'
const updateCheckingMessage = '正在检查云端版本...'
const routeViewLoaders: Record<string, () => Promise<unknown>> = {
  '/': () => import('@/views/Dashboard.vue'),
  '/accounts': () => import('@/views/Accounts.vue'),
  '/logs': () => import('@/views/Logs.vue'),
  '/gallery': () => import('@/views/Gallery.vue'),
  '/monitor': () => import('@/views/Monitor.vue'),
  '/cluster': () => import('@/views/Cluster.vue'),
  '/proxy': () => import('@/views/Proxy.vue'),
  '/settings': () => import('@/views/Settings.vue'),
  '/register': () => import('@/views/Register.vue'),
  '/debug': () => import('@/views/DebugCenter.vue'),
  '/studio': () => import('@/views/Studio.vue'),
}

watch(
  () => route.path,
  () => {
    isSidebarOpen.value = false
  }
)

isSidebarCollapsed.value = getBooleanPreference(preferenceKeys.sidebarCollapsed, false)

watch(isSidebarCollapsed, (value) => {
  setBooleanPreference(preferenceKeys.sidebarCollapsed, value)
})

function refreshPage() {
  window.location.reload()
}

async function handleLogout() {
  await authStore.logout()
  await router.replace({ name: 'login' })
}

async function openApiInfo() {
  currentAuthToken.value = getAuthToken()
  isApiInfoOpen.value = true
  if (!settingsStore.settings && !settingsStore.isLoading) {
    await settingsStore.loadSettings()
  }
  await loadModelCatalog()
}

async function copyText(value: string) {
  const text = String(value || '').trim()
  if (!text) return
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      const input = document.createElement('textarea')
      input.value = text
      input.setAttribute('readonly', 'readonly')
      input.style.position = 'fixed'
      input.style.left = '-9999px'
      input.style.top = '0'
      document.body.appendChild(input)
      input.focus()
      input.select()
      input.setSelectionRange(0, input.value.length)
      const copied = document.execCommand('copy')
      document.body.removeChild(input)
      if (!copied) throw new Error('execCommand copy failed')
    }
    toast.success('已复制')
  } catch (error) {
    console.error('Copy failed', error)
    toast.error('复制失败，请手动复制')
  }
}

async function openInfiniteCanvas() {
  if (!canvasHref.value) return
  const ok = await confirmDialog.ask({
    title: '打开无限画布',
    message: '即将打开外部画布，仅附带 OpenAI 兼容接口地址，不会附带当前调用密钥。是否继续？',
    confirmText: '打开',
    cancelText: '取消',
  })
  if (ok) {
    window.open(canvasHref.value, '_blank', 'noopener,noreferrer')
  }
}

function setThemeMode(mode: ThemeMode) {
  themeMode.value = mode
  setStoredThemeMode(mode)
}

function cycleThemeMode() {
  const index = themeOptions.findIndex(option => option.value === themeMode.value)
  const next = themeOptions[(index + 1) % themeOptions.length]
  setThemeMode(next.value)
}

function openUpdateDialog() {
  isUpdateDialogOpen.value = true
  updateCheckMessage.value = updateCheckingMessage
  updateCheckStatus.value = 'checking'
  void loadLocalReleaseEntries()
  void checkForUpdates(false)
}

function openReleasePage() {
  window.open(releasePageUrl, '_blank', 'noopener,noreferrer')
}

async function checkForUpdates(showMessage = true) {
  if (isCheckingUpdate.value) return
  isCheckingUpdate.value = true
  updateCheckStatus.value = 'checking'
  updateCheckMessage.value = updateCheckingMessage
  const [versionResult, changelogResult] = await Promise.allSettled([
    fetchRemoteText(latestVersionUrl),
    fetchRemoteText(latestChangelogUrl),
  ])

  if (versionResult.status === 'fulfilled') {
    latestVersionTag.value = normalizeVersionTag(versionResult.value)
  }
  if (changelogResult.status === 'fulfilled') {
    const remoteReleases = parseChangelog(changelogResult.value)
    if (remoteReleases.length) {
      releaseEntries.value = remoteReleases
      releaseEntriesSource.value = 'remote'
    }
  }

  const fullySuccessful = versionResult.status === 'fulfilled' && changelogResult.status === 'fulfilled'
  const fullyFailed = versionResult.status === 'rejected' && changelogResult.status === 'rejected'
  const newVersionAvailable = hasNewVersion.value
  if (fullyFailed) {
    updateCheckStatus.value = 'warning'
    updateCheckMessage.value = '云端版本与更新日志获取失败，当前展示本地信息。'
  } else if (!fullySuccessful) {
    updateCheckStatus.value = newVersionAvailable ? 'available' : 'warning'
    updateCheckMessage.value = newVersionAvailable
      ? `发现新版本：${latestVersionLabel.value}（部分云端信息获取失败）`
      : '部分云端信息获取失败，已展示可用的版本信息。'
  } else if (newVersionAvailable) {
    updateCheckStatus.value = 'available'
    updateCheckMessage.value = `发现新版本：${latestVersionLabel.value}`
  } else {
    updateCheckStatus.value = 'current'
    updateCheckMessage.value = `当前已是最新版本：${currentVersionLabel.value || latestVersionLabel.value}`
  }

  if (showMessage) {
    if (fullyFailed || !fullySuccessful) toast.warning(updateCheckMessage.value)
    else if (newVersionAvailable) toast.info(updateCheckMessage.value)
    else toast.success(updateCheckMessage.value)
  }
  isCheckingUpdate.value = false
}

async function fetchRemoteText(url: string) {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), 8000)
  try {
    const response = await fetch(url, { cache: 'no-store', signal: controller.signal })
    if (!response.ok) throw new Error(`云端返回 ${response.status}`)
    return response.text()
  } finally {
    window.clearTimeout(timeoutId)
  }
}

async function loadLocalReleaseEntries() {
  if (releaseEntriesSource.value !== 'none') return
  try {
    const module = await import('../../../CHANGELOG.md?raw')
    const localReleases = parseChangelog(module.default || '')
    if (releaseEntriesSource.value === 'none') {
      releaseEntries.value = localReleases
      releaseEntriesSource.value = 'local'
    }
  } catch {
    if (releaseEntriesSource.value === 'none') {
      releaseEntries.value = []
    }
  }
}

async function loadCurrentVersion() {
  try {
    const result = await versionApi.current()
    const runtimeVersion = String(result.tag || '').trim()
    if (runtimeVersion) currentVersionTag.value = runtimeVersion
    if (!latestVersionTag.value) {
      latestVersionTag.value = normalizeVersionTag(latestReleasedVersion(releaseEntries.value) || currentVersionTag.value)
    }
  } catch {
    if (!latestVersionTag.value) {
      latestVersionTag.value = normalizeVersionTag(latestReleasedVersion(releaseEntries.value) || currentVersionTag.value)
    }
  }
}

function handleSystemThemeChange() {
  if (themeMode.value === 'system') {
    applyThemeMode(themeMode.value)
  }
}

function setupSystemThemeListener() {
  if (typeof window === 'undefined') return
  systemThemeMedia = window.matchMedia('(prefers-color-scheme: dark)')
  systemThemeMedia.addEventListener('change', handleSystemThemeChange)
}

async function loadThirdPartyApps() {
  try {
    thirdPartyApps.value = await settingsApi.getThirdPartyApps()
  } catch {
    thirdPartyApps.value = null
  }
}

function normalizedRoutePath(path: string) {
  if (!path || path === '/') return '/'
  return `/${path.replace(/^\/+/, '').split(/[?#]/)[0]}`
}

function prefetchRouteView(path: string) {
  const normalizedPath = normalizedRoutePath(path)
  const loader = routeViewLoaders[normalizedPath]
  if (!loader || prefetchedRoutePaths.has(normalizedPath)) return
  prefetchedRoutePaths.add(normalizedPath)
  void loader().catch(() => {
    prefetchedRoutePaths.delete(normalizedPath)
  })
}

function handleNavClick() {
  isSidebarOpen.value = false
}

function stopRoutePending() {
  if (routePendingTimer !== null) {
    window.clearTimeout(routePendingTimer)
    routePendingTimer = null
  }
  isRoutePending.value = false
}

function startRoutePending(title: string) {
  stopRoutePending()
  pendingRouteTitle.value = title
  routePendingTimer = window.setTimeout(() => {
    isRoutePending.value = true
  }, 120)
}

function setupRoutePendingGuards() {
  stopRoutePendingBeforeEach = router.beforeEach((to, from) => {
    if (to.fullPath !== from.fullPath) {
      startRoutePending(titleForRoute(to.name, to.path))
    }
    return true
  })
  stopRoutePendingAfterEach = router.afterEach(() => {
    stopRoutePending()
  })
  stopRoutePendingError = router.onError(() => {
    stopRoutePending()
  })
}

function teardownRoutePendingGuards() {
  stopRoutePendingBeforeEach?.()
  stopRoutePendingAfterEach?.()
  stopRoutePendingError?.()
  stopRoutePendingBeforeEach = null
  stopRoutePendingAfterEach = null
  stopRoutePendingError = null
  stopRoutePending()
}

onMounted(() => {
  applyThemeMode(themeMode.value)
  setupSystemThemeListener()
  setupRoutePendingGuards()
  void loadCurrentVersion()
  void loadThirdPartyApps()
})

onBeforeUnmount(() => {
  systemThemeMedia?.removeEventListener('change', handleSystemThemeChange)
  systemThemeMedia = null
  teardownRoutePendingGuards()
})

</script>

<style scoped>
.route-view-content {
  min-width: 0;
}

.route-pending-bar {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  z-index: 80;
  height: 2px;
  overflow: hidden;
  background: hsl(var(--primary) / 0.16);
  pointer-events: none;
}

.route-pending-bar::after {
  display: block;
  width: 100%;
  height: 100%;
  content: '';
  background: hsl(var(--primary));
  box-shadow: 0 0 14px hsl(var(--primary) / 0.3);
  animation: route-pending-pulse 0.9s ease-in-out infinite alternate;
}

@keyframes route-pending-pulse {
  from { opacity: 0.36; }
  to { opacity: 1; }
}
</style>
