<template>
  <div class="chatbot-page">
    <div v-if="!status.checked.value" class="chatbot-fill">
      <v-progress-circular indeterminate size="48" width="4" />
    </div>

    <ModuleOffline
      v-else-if="!status.reachable.value"
      module-name="stream"
      :on-started="status.check"
    />

    <template v-else>
      <v-alert
        v-if="persona.error.value"
        type="error"
        variant="tonal"
        density="compact"
        closable
        class="mb-4"
        @click:close="persona.error.value = ''"
      >
        {{ persona.error.value }}
      </v-alert>

      <div class="d-flex align-center flex-wrap ga-2 mb-1">
        <div class="text-h5">Chatbot</div>
        <v-spacer />
        <v-chip size="small" variant="outlined">{{ persona.coreCount.value }} core blocks</v-chip>
        <v-chip size="small" variant="tonal" color="primary">
          {{ persona.coreUsed.value }} core chars
        </v-chip>
        <v-btn
          size="small"
          variant="tonal"
          color="warning"
          prepend-icon="mdi-restoration"
          title="把 chatbot 的 Prompt 与 Core Memory 一起恢复为内置默认"
          :disabled="everythingDefault"
          :loading="persona.busy.value && pending === 'all'"
          @click="resetAll"
        >
          恢复默认人设
        </v-btn>
      </div>
      <div class="text-caption text-disabled mb-4">
        模拟观众（chat）的人设：system prompt 模板 + 观众视角的 Core Memory。
        存 stream 数据库，仅人工编辑，保存后下一轮生成即生效。
      </div>

      <v-card variant="tonal" class="mb-4">
        <v-card-text class="d-flex align-center flex-wrap ga-3 py-2">
          <div class="text-overline">Runtime</div>
          <v-chip size="x-small" :color="runtime.enabled ? 'success' : 'grey'" variant="tonal">
            {{ runtime.enabled ? 'enabled' : 'disabled' }}
          </v-chip>
          <v-chip size="x-small" variant="outlined">model {{ runtime.model || '—' }}</v-chip>
          <v-chip size="x-small" variant="outlined">
            interval {{ runtime.interval_s }}s
          </v-chip>
          <v-chip size="x-small" variant="outlined">count {{ runtime.count }}</v-chip>
          <v-chip size="x-small" variant="outlined">
            concurrent {{ runtime.in_flight }}/{{ runtime.max_concurrent }}
          </v-chip>
          <v-spacer />
          <v-btn
            size="x-small"
            variant="text"
            prepend-icon="mdi-cog-outline"
            to="/config"
            title="开关 / 模型 / 间隔 / 条数在 Configuration → Stream 的 Chatbot 卡里改"
          >
            运行时参数
          </v-btn>
        </v-card-text>
      </v-card>

      <v-tabs v-model="tab" density="compact" class="mb-4">
        <v-tab value="prompt">Prompt</v-tab>
        <v-tab value="core">Core Memory</v-tab>
      </v-tabs>

      <PromptEditor
        v-if="tab === 'prompt'"
        :content="persona.prompt.value.content"
        :updated-at="persona.prompt.value.updated_at"
        :is-default="persona.prompt.value.is_default"
        :busy="persona.busy.value"
        :placeholders="persona.placeholders.value"
        :rows="20"
        :api="promptApi"
        preview-title="编译预览（chatbot system prompt）"
        reset-label="恢复默认 Prompt"
        reset-confirm="把 chatbot 的 Prompt 模板恢复为内置默认？当前内容会丢失（Core Memory 不受影响）。"
      />

      <CoreMemoryList
        v-else
        :blocks="persona.blocks.value"
        :busy="persona.busy.value"
        :is-default="persona.coreIsDefault.value"
        :api="coreApi"
        hint="观众视角的共同知识：人物关系、社区梗、大事记。仅人工编辑，chatbot 没有写工具。"
      />
    </template>
  </div>
</template>

<script lang="ts" setup>
import { computed, ref, watch } from 'vue'
import { usePersona } from '@/composables/usePersona'
import { useModuleStatus } from '@/composables/useModuleStatus'
import { useStream } from '@/composables/useStream'
import ModuleOffline from '@/components/ModuleOffline.vue'
import PromptEditor from '@/components/persona/PromptEditor.vue'
import CoreMemoryList from '@/components/persona/CoreMemoryList.vue'

// chatbot 人设与 neuro 人设协议一致，只是走 stream 的代理路径
const persona = usePersona('/manage/stream/manage/chatbot/persona')
const status = useModuleStatus('stream')
const st = useStream()

const tab = ref<'prompt' | 'core'>('prompt')
const pending = ref<'all' | null>(null)

const runtime = computed(
  () =>
    st.status.value?.sections.chatbot.current ?? {
      enabled: false,
      model: null,
      model_ref: null,
      interval_s: 0,
      count: 0,
      max_concurrent: 0,
      interval_jitter: 0,
      in_flight: 0,
      discarded: 0,
      last_discard: null,
      template_error: null,
    },
)

const everythingDefault = computed(
  () => persona.prompt.value.is_default && persona.coreIsDefault.value,
)

const promptApi = {
  save: (content: string) => persona.savePrompt(content),
  reset: () => persona.resetPrompt(),
  preview: (draft: string) => persona.preview(draft),
}

const coreApi = {
  add: (title: string, content: string) => persona.addCore(title, content),
  update: (id: number, title: string, content: string) => persona.updateCore(id, title, content),
  remove: (id: number) => persona.removeCore(id),
  reset: () => persona.resetCore(),
}

watch(
  () => status.reachable.value,
  (reachable) => {
    if (reachable) {
      void persona.load()
      void st.refreshStatus()
    }
  },
  { immediate: true },
)

async function resetAll() {
  if (
    !window.confirm(
      '恢复 chatbot 默认人设？\n\n- Prompt 模板 → 内置默认\n- Core Memory → 内置默认块\n\n当前的自定义内容会全部丢失，且无法撤销。',
    )
  )
    return
  pending.value = 'all'
  await persona.resetAll()
  pending.value = null
}
</script>

<style scoped>
.chatbot-page {
  max-width: 1080px;
  margin: 0 auto;
  padding: 8px 0 32px;
}
.chatbot-fill {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 50vh;
}
</style>
