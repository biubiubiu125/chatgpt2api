<template>
  <ModalShell
    :open="open"
    max-width="36rem"
    :z-index="140"
    :close-on-backdrop="!restoring"
    @close="$emit('close')"
  >
    <ModalHeader
      title="恢复图片任务备份"
      subtitle="校验备份后恢复图片队列记录和图片文件。"
      :close-disabled="restoring"
      :bordered="false"
      @close="$emit('close')"
    />
    <ModalBody class="space-y-3">
      <div class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
        恢复会写入当前图片队列数据库和图片目录；如果目标已有不同内容会失败并回滚已写入的图片文件。系统配置、账号快照、日志等备份内容不会在这里覆盖。
      </div>
      <div class="rounded-xl border border-border bg-background px-3 py-2 text-xs">
        <p class="font-medium text-foreground">{{ itemTitle }}</p>
        <p class="mt-1 break-all font-mono text-muted-foreground">{{ itemKey }}</p>
      </div>
      <FormField label="加密口令（可选）">
        <Input
          v-model="passphraseModel"
          type="password"
          block
          placeholder="留空则使用当前保存的备份口令"
          :disabled="restoring"
          @keydown.enter.prevent="$emit('restore')"
        />
      </FormField>
    </ModalBody>
    <ModalFooter :bordered="false">
      <Button size="sm" variant="outline" :disabled="restoring" @click="$emit('close')">取消</Button>
      <Button size="sm" variant="primary" :disabled="restoring || !item" @click="$emit('restore')">
        {{ restoring ? '恢复中...' : '开始恢复' }}
      </Button>
    </ModalFooter>
  </ModalShell>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Button, FormField, Input } from 'nanocat-ui'
import ModalBody from '@/components/ai/ModalBody.vue'
import ModalFooter from '@/components/ai/ModalFooter.vue'
import ModalHeader from '@/components/ai/ModalHeader.vue'
import ModalShell from '@/components/ai/ModalShell.vue'
import type { BackupItem } from '@/api/settings'

const props = defineProps<{
  open: boolean
  item: BackupItem | null
  passphrase: string
  restoring: boolean
}>()

const emit = defineEmits<{
  close: []
  restore: []
  'update:passphrase': [value: string]
}>()

const passphraseModel = computed({
  get: () => props.passphrase,
  set: (value) => emit('update:passphrase', String(value || '')),
})

const itemTitle = computed(() => props.item?.name || props.item?.key || '未选择备份')
const itemKey = computed(() => props.item?.key || '-')
</script>
