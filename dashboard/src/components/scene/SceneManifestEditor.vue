<script setup lang="ts">
/**
 * 场景清单编辑器：直接编辑模块 DB 里的那份场景 JSON。
 *
 * 场景数据是**服务端的数据**（存在各模块自己的 DB 里，包内 defaults 只是首次
 * 播种的模板）。这个编辑器把那份 JSON 原样呈现出来，改完 PUT 给模块：
 * 服务端校验 → 落库 → 广播 `manifest_changed` → 画面页自动重载。
 *
 * 两个模块的协议一致，所以这里做成一个组件、传不同的 `base`：
 *   <SceneManifestEditor base="/manage/stream" ... />
 *   <SceneManifestEditor base="/manage/neuro_sama" ... />
 */
  import { computed, ref, watch } from 'vue'
  import { api, describeError } from '@/stores/connection'

  const props = defineProps<{
    /** 模块的 vedal 代理前缀（/manage/<module>） */
    base: string
    /** 对话框是否打开 */
    modelValue: boolean
    /** 标题 */
    title?: string
  }>()

  const emit = defineEmits<{
    (e: 'update:modelValue', v: boolean): void
    (e: 'saved'): void
  }>()

  const open = computed({
    get: () => props.modelValue,
    set: (v: boolean) => emit('update:modelValue', v),
  })

  const draft = ref('')
  const saved = ref('')
  const source = ref<{ is_default: boolean, updated_at: string, chars: number } | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')
  const notice = ref('')

  const dirty = computed(() => draft.value !== saved.value)

  async function reload () {
    loading.value = true
    error.value = ''
    notice.value = ''
    try {
      const [manifest, meta] = await Promise.all([
        api<Record<string, unknown>>(`${props.base}/ui/manifest`),
        api<{ is_default: boolean, updated_at: string, chars: number }>(
          `${props.base}/ui/manifest/source`,
        ),
      ])
      saved.value = JSON.stringify(manifest, null, 2)
      draft.value = saved.value
      source.value = meta
    } catch (error_) {
      error.value = describeError(error_)
    } finally {
      loading.value = false
    }
  }

  async function save () {
    saving.value = true
    error.value = ''
    notice.value = ''
    try {
      const resp = await api<{ status: string, source: typeof source.value }>(
        `${props.base}/ui/manifest`,
        { method: 'PUT', body: JSON.stringify({ content: draft.value }) },
      )
      source.value = resp.source
      saved.value = JSON.stringify(JSON.parse(draft.value), null, 2)
      draft.value = saved.value
      notice.value = '已保存。画面页会收到通知并自动重载。'
      emit('saved')
    } catch (error_) {
      error.value = describeError(error_)
    } finally {
      saving.value = false
    }
  }

  async function resetToDefault () {
    saving.value = true
    error.value = ''
    notice.value = ''
    try {
      const resp = await api<{ status: string, source: typeof source.value }>(
        `${props.base}/ui/manifest/reset`,
        { method: 'POST' },
      )
      source.value = resp.source
      await reload()
      notice.value = '已恢复内置默认。'
      emit('saved')
    } catch (error_) {
      error.value = describeError(error_)
    } finally {
      saving.value = false
    }
  }

  watch(
    () => props.modelValue,
    v => {
      if (v) void reload()
    },
    { immediate: true },
  )
</script>

<template>
  <v-dialog v-model="open" max-width="900" scrollable>
    <v-card>
      <v-card-title class="text-body-1 font-weight-medium">
        {{ title || '场景数据' }}
        <v-spacer />
        <v-chip v-if="source" class="ml-2" size="x-small" variant="tonal">
          {{ source.is_default ? '内置默认' : '已修改' }}
        </v-chip>
      </v-card-title>

      <v-card-text>
        <div class="text-caption text-grey-darken-1 mb-2">
          场景数据存在模块自己的数据库里（包内 defaults 只是首次播种的模板）。
          改的是**画面**的数据：元素、几何、脚本、neuro 窗口层都在里面。
          保存后服务端会校验，画面页收到通知自动重载。
        </div>

        <v-alert
          v-if="error"
          class="mb-2"
          density="compact"
          type="error"
          variant="tonal"
        >
          <pre class="text-wrap">{{ error }}</pre>
        </v-alert>
        <v-alert
          v-if="notice"
          class="mb-2"
          density="compact"
          type="success"
          variant="tonal"
        >
          {{ notice }}
        </v-alert>

        <v-textarea
          v-model="draft"
          class="scene-editor__area"
          density="compact"
          :disabled="loading || saving"
          placeholder="场景 JSON"
          rows="18"
          spellcheck="false"
          variant="outlined"
        />

        <div class="text-caption text-grey-darken-1 mt-1">
          <span v-if="source">最后更新：{{ source.updated_at || '—' }} · </span>
          {{ draft.length }} 字符<span v-if="dirty"> · 有未保存的修改</span>
        </div>
      </v-card-text>

      <v-card-actions>
        <v-btn :disabled="loading || saving" size="small" variant="text" @click="reload">
          重新读取
        </v-btn>
        <v-btn
          :disabled="loading || saving"
          size="small"
          title="用包内的默认清单覆盖当前内容"
          variant="text"
          @click="resetToDefault"
        >
          恢复默认
        </v-btn>
        <v-spacer />
        <v-btn size="small" variant="text" @click="open = false">关闭</v-btn>
        <v-btn
          color="primary"
          :disabled="!dirty || loading || saving"
          :loading="saving"
          size="small"
          @click="save"
        >
          保存
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.scene-editor__area :deep(textarea) {
  font-family: 'Roboto Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
  tab-size: 2;
}
</style>
