<template>
  <div class="space-y-4">
    <FormSection title="账号策略">
      <div class="settings-check-grid settings-check-grid--single">
        <div class="settings-check-item">
          <div class="settings-check-control">
            <Checkbox v-model="settings.auto_remove_invalid_accounts">自动移除异常账号</Checkbox>
            <HelpTip text="确认鉴权无效的账号会进入异常处理；开启后直接移除，关闭后保留异常状态。" />
          </div>
        </div>
        <div class="settings-check-item">
          <div class="settings-check-control">
            <Checkbox v-model="settings.auto_remove_rate_limited_accounts">自动移除额度耗尽账号</Checkbox>
            <HelpTip text="只有远程明确确认图片额度为 0 时才会处理，代理错误、断流或上游 429 不会删除账号。" />
          </div>
        </div>
        <div class="settings-check-item">
          <div class="settings-check-control">
            <Checkbox v-model="settings.image_account_retry_enabled">生图失败后尝试其他账号</Checkbox>
            <HelpTip text="除文本结果（HTTP 400）外，当前账号未能交付图片时立即尝试其他账号；当前请求不会等待后台账号核验。" />
          </div>
        </div>
        <div class="settings-check-item">
          <div class="settings-check-control">
            <Checkbox v-model="settings.image_preflight_token_refresh_enabled">生图前强制刷新 Access Token</Checkbox>
            <HelpTip text="默认关闭。开启后，带 refresh_token 的账号会在生图前额外刷新 Access Token；仅有 Access Token 的账号会跳过。并发请求会复用正在进行的刷新，已有生图任务时会跳过主动轮换。" />
          </div>
        </div>
      </div>
      <FormField label="最大尝试账号数">
        <template #label-extra>
          <HelpTip text="包含第一次使用的账号。默认 4，最小 2，不限制可填写的最大值。" />
        </template>
        <Input
          :model-value="imageMaxAccountAttemptsField.input.value"
          type="number"
          block
          placeholder="4"
          :disabled="!settings.image_account_retry_enabled"
          @update:model-value="imageMaxAccountAttemptsField.update"
        />
      </FormField>
      <FormField label="图片鉴权并发数">
        <template #label-extra>
          <HelpTip text="限制生图前刷新和失败后后台核验的并发请求。默认 10，最小 1，不限制可填写的最大值；不影响文本、搜索和定时账号刷新。" />
        </template>
        <Input
          :model-value="imageAuthRefreshConcurrencyField.input.value"
          type="number"
          block
          placeholder="10"
          @update:model-value="imageAuthRefreshConcurrencyField.update"
        />
      </FormField>
    </FormSection>

    <FormSection title="图片确认">
      <div class="settings-check-grid settings-check-grid--single">
        <div class="settings-check-item">
          <div class="settings-check-control">
            <Checkbox v-model="settings.image_settle_enabled">图片二次确认机制</Checkbox>
            <HelpTip text="找到图片结果后再等待指定秒数复查一次，减少结果尚未稳定时提前返回。" />
          </div>
        </div>
        <div class="settings-check-item">
          <div class="settings-check-control">
            <Checkbox v-model="settings.image_remove_conversation_after_result">图片成功后删除官网会话</Checkbox>
            <HelpTip text="默认关闭。仅在图片已成功保存后尝试隐藏 ChatGPT 官网 conversation；失败只记录日志，不影响图片返回。关闭时保留官网会话，便于恢复和排查。" />
          </div>
        </div>
      </div>
      <FormField label="二次确认等待（秒）">
        <Input
          :model-value="imageSettleSecondsField.input.value"
          type="number"
          block
          placeholder="5"
          :disabled="!settings.image_settle_enabled"
          @update:model-value="imageSettleSecondsField.update"
        />
      </FormField>
    </FormSection>

    <FormSection title="图片放大">
      <div class="settings-check-grid settings-check-grid--single">
        <div class="settings-check-item">
          <div class="settings-check-control">
            <Checkbox v-model="settings.image_upscale_enabled">图片尺寸不足时自动放大</Checkbox>
            <HelpTip text="默认关闭。仅处理明确请求了尺寸且上游图片小于目标尺寸的结果；自动尺寸和已达到目标尺寸的图片保持原样。" />
          </div>
        </div>
      </div>
      <FormField label="放大引擎">
        <GroupedSelectMenu
          v-model="settings.image_upscale_engine"
          :options="imageUpscaleEngineOptions"
          :disabled="!settings.image_upscale_enabled"
          selected-indicator="none"
          aria-label="图片放大引擎"
          block
        />
      </FormField>
    </FormSection>

    <FormSection title="控制台日志级别">
      <div class="settings-check-grid settings-check-grid--single mt-3">
        <div
          v-for="level in logLevelOptions"
          :key="level"
          class="settings-check-item"
        >
          <div class="settings-check-control">
            <Checkbox
              :model-value="settings.log_levels.includes(level)"
              @update:model-value="$emit('setLogLevel', level, Boolean($event))"
            >
              {{ level }}
            </Checkbox>
            <HelpTip v-if="level === 'debug'" text="不选择任何级别时使用默认 info / warning / error。" />
          </div>
        </div>
      </div>
    </FormSection>
  </div>
</template>

<script setup lang="ts">
import { Checkbox, FormField, FormSection, HelpTip, Input } from 'nanocat-ui'
import GroupedSelectMenu from '@/components/ui/GroupedSelectMenu.vue'
import type { Settings } from '@/types/api'
import { imageUpscaleEngineOptions, logLevelOptions } from '@/views/settings/settingsView'
import type { NumberSettingField } from '@/views/settings/useNumberSettingField'

defineProps<{
  settings: Settings
  imageAuthRefreshConcurrencyField: NumberSettingField
  imageMaxAccountAttemptsField: NumberSettingField
  imageSettleSecondsField: NumberSettingField
}>()

defineEmits<{
  setLogLevel: [level: string, enabled: boolean]
}>()
</script>

<style scoped>
.settings-check-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(13.5rem, 1fr));
  gap: 8px;
}

.settings-check-grid--single {
  grid-template-columns: minmax(0, 1fr);
}

.settings-check-item {
  min-height: 38px;
  border: 1px solid hsl(var(--border));
  border-radius: 14px;
  background: hsl(var(--background) / 0.72);
  transition:
    border-color 0.16s ease,
    background-color 0.16s ease;
}

.settings-check-item:hover {
  border-color: hsl(var(--foreground) / 0.18);
  background: hsl(var(--muted) / 0.24);
}

.settings-check-control {
  display: flex;
  min-height: 38px;
  align-items: center;
  gap: 8px;
  padding-right: 10px;
}

.settings-check-item :deep(label) {
  display: flex;
  width: 100%;
  flex: 1;
  min-height: 38px;
  align-items: center;
  gap: 10px;
  padding: 9px 11px;
}

.settings-check-item :deep(label > span:last-child) {
  color: hsl(var(--foreground) / 0.78);
  line-height: 1.35;
}
</style>
