<template>
  <div class="prompt-editor">
    <StatusPathPicker
      :mode="mode"
      :placeholders="placeholders"
      @insert="insertAtCursor"
    />
    <div v-if="appendHint" class="text-caption text-disabled mb-2">{{ appendHint }}</div>

    <div class="d-flex align-center flex-wrap ga-2 mb-2">
      <v-chip size="small" variant="outlined">{{ draft.length }} chars</v-chip>
      <v-chip v-if="dirty" size="small" color="warning" variant="tonal">未保存</v-chip>
      <v-chip v-else size="small" color="success" variant="tonal">已保存</v-chip>
      <v-chip
        v-if="missing.length"
        size="small"
        color="warning"
        variant="tonal"
        :title="'缺少占位符（仅提示，不阻止保存）：' + missing.join('、')"
      >
        缺少 {{ missing.length }} 个占位符
      </v-chip>
      <span v-if="updatedAt" class="text-caption text-disabled">updated {{ updatedAt }}</span>
      <v-spacer />
      <v-btn
        size="small"
        variant="text"
        prepend-icon="mdi-eye-outline"
        :loading="previewing"
        :disabled="!draft.trim()"
        @click="openPreview"
      >
        {{ previewLabel }}
      </v-btn>
    </div>

    <v-textarea
      ref="inputEl"
      v-model="draft"
      class="prompt-editor__input"
      variant="outlined"
      :rows="rows"
      hide-details
      spellcheck="false"
    />

    <div class="d-flex align-center flex-wrap ga-2 mt-3">
      <v-btn
        color="primary"
        :disabled="!dirty || !draft.trim()"
        :loading="busy && pending === 'save'"
        @click="save"
      >
        保存
      </v-btn>
      <v-btn variant="text" :disabled="!dirty" @click="discard">放弃更改</v-btn>
      <v-spacer />
      <v-btn
        variant="text"
        color="warning"
        prepend-icon="mdi-restoration"
        :disabled="isDefault && !dirty"
        :loading="busy && pending === 'reset'"
        @click="reset"
      >
        {{ resetLabel }}
      </v-btn>
    </div>

    <v-dialog v-model="previewDialog" max-width="1100">
      <v-card v-if="previewData">
        <v-card-title class="d-flex align-center flex-wrap ga-2">
          <span>{{ previewTitle }}</span>
          <v-chip size="small" variant="outlined">{{ previewData.chars.total }} chars</v-chip>
          <v-chip v-if="previewData.estimated_tokens" size="small" variant="tonal">
            ~{{ previewData.estimated_tokens }} tokens
          </v-chip>
          <v-chip v-if="previewData.chars.core" size="small" variant="tonal" color="primary">
            core {{ previewData.chars.core }}
          </v-chip>
          <v-chip
            v-for="[key, len] in Object.entries(previewData.chars.sections || {})"
            :key="key"
            size="small"
            variant="tonal"
          >
            {{ key }} {{ len }}
          </v-chip>
          <v-chip
            v-for="[key, len] in Object.entries(previewData.expanded || {})"
            :key="'exp-' + key"
            size="small"
            variant="tonal"
            color="info"
          >
            {{ key }} {{ len }} chars
          </v-chip>
          <v-spacer />
          <v-btn
            icon="mdi-content-copy"
            size="small"
            variant="text"
            @click="copy(previewData.prompt)"
          />
        </v-card-title>
        <v-card-text>
          <v-alert
            v-if="previewData.rendered === false"
            type="error"
            variant="tonal"
            density="compact"
            class="mb-2"
          >
            模板渲染失败（直播会退回上一版文本）：{{ previewData.error }}
          </v-alert>
          <v-alert
            v-if="previewData.unresolved_paths?.length"
            type="warning"
            variant="tonal"
            density="compact"
            class="mb-2"
          >
            这些路径在当前投影里不存在（渲染为空串，仅提示）：
            <code v-for="p in previewData.unresolved_paths" :key="p" class="mr-2">{{ p }}</code>
          </v-alert>
          <v-alert
            v-if="previewData.missing_placeholders?.length"
            type="warning"
            variant="tonal"
            density="compact"
            class="mb-2"
          >
            缺少占位符（仅提示，不阻止保存）：
            <code v-for="p in previewData.missing_placeholders" :key="p" class="mr-2">{{ p }}</code>
          </v-alert>
          <pre class="prompt-editor__preview">{{ previewData.prompt }}</pre>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="previewDialog = false">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="snackbar" :timeout="1800" location="bottom">{{ snackbarText }}</v-snackbar>
  </div>
</template>

<script lang="ts" setup>
import { computed, nextTick, ref, watch } from 'vue'
import type { PlaceholderInfo, PreviewResult } from '@/types/persona'
import StatusPathPicker from '@/components/persona/StatusPathPicker.vue'

/** 人设/模板共用的 prompt 编辑器：草稿状态、脏检测、占位符提示、编译预览、恢复默认。
 *  具体读写由父页面通过 api 传入（组件不关心是哪个人设）。
 *  `mode` 决定顶部选择器：template=状态路径树（消息模板）；persona=真实占位符。 */
const props = withDefaults(
  defineProps<{
    content: string
    updatedAt?: string
    isDefault?: boolean
    busy?: boolean
    placeholders?: PlaceholderInfo[]
    required?: string[]
    rows?: number
    previewLabel?: string
    previewTitle?: string
    resetLabel?: string
    resetConfirm?: string
    appendHint?: string
    mode?: 'template' | 'persona'
    api: {
      save: (content: string) => Promise<boolean>
      reset: () => Promise<boolean>
      preview: (draft: string) => Promise<PreviewResult | null>
    }
  }>(),
  {
    updatedAt: '',
    isDefault: false,
    busy: false,
    placeholders: () => [],
    required: () => [],
    rows: 22,
    previewLabel: '预览编译结果',
    previewTitle: '编译预览',
    resetLabel: '恢复默认',
    resetConfirm: '恢复内置默认内容？当前内容会丢失。',
    appendHint: '',
    mode: 'persona',
  },
)

const emit = defineEmits<{ (e: 'saved'): void; (e: 'reset-done'): void }>()

const draft = ref(props.content)
const inputEl = ref<{ $el?: Element } | null>(null)
const pending = ref<'save' | 'reset' | null>(null)
const previewing = ref(false)
const previewDialog = ref(false)
const previewData = ref<PreviewResult | null>(null)
const snackbar = ref(false)
const snackbarText = ref('')

const dirty = computed(() => draft.value !== props.content)
// 草稿里缺占位符只做提示（服务端也不再拒绝保存）；前端不拦保存
const missing = computed(() =>
  (props.required || []).filter((key) => !draft.value.includes(`{{${key}}}`)),
)

/** 在 textarea 光标处插入（选择器的主操作；剪贴板只是次级操作）。 */
function insertAtCursor(text: string) {
  const el = inputEl.value?.$el?.querySelector('textarea') as HTMLTextAreaElement | null
  if (!el) {
    draft.value += text
    return
  }
  const start = el.selectionStart ?? draft.value.length
  const end = el.selectionEnd ?? start
  draft.value = draft.value.slice(0, start) + text + draft.value.slice(end)
  void nextTick(() => {
    el.focus()
    const pos = start + text.length
    el.setSelectionRange(pos, pos)
  })
}

watch(
  () => props.content,
  (value) => {
    draft.value = value
  },
)

function notify(text: string) {
  snackbarText.value = text
  snackbar.value = true
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    notify('已复制到剪贴板')
  } catch {
    notify('复制失败（浏览器未授权剪贴板）')
  }
}

async function save() {
  if (!draft.value.trim()) return
  pending.value = 'save'
  const ok = await props.api.save(draft.value)
  pending.value = null
  if (ok) {
    notify('已保存，下一轮对话生效')
    emit('saved')
  }
}

function discard() {
  draft.value = props.content
}

async function reset() {
  if (!window.confirm(props.resetConfirm)) return
  pending.value = 'reset'
  const ok = await props.api.reset()
  pending.value = null
  if (ok) {
    notify('已恢复内置默认')
    emit('reset-done')
  }
}

async function openPreview() {
  previewing.value = true
  const data = await props.api.preview(draft.value)
  previewing.value = false
  if (data) {
    previewData.value = data
    previewDialog.value = true
  }
}
</script>

<style scoped>
.prompt-editor__input :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace;
  font-size: 12.5px;
  line-height: 1.55;
}
.prompt-editor__preview {
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  line-height: 1.55;
  max-height: 62vh;
  overflow: auto;
  margin: 0;
  padding: 12px;
  border-radius: 6px;
  background: rgba(var(--v-theme-surface-variant), 0.5);
}
</style>
