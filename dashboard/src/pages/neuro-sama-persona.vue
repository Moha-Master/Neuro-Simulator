<template>
  <div class="persona-page">
    <div v-if="!status.checked.value" class="persona-fill">
      <v-progress-circular indeterminate size="48" width="4" />
    </div>

    <ModuleOffline
      v-else-if="!status.reachable.value"
      module-name="neuro_sama"
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
        <div class="text-h5">Persona</div>
        <v-spacer />
        <v-chip size="small" variant="outlined">
          {{ persona.coreCount.value }} core blocks
        </v-chip>
        <v-chip size="small" variant="tonal" color="primary">
          {{ persona.coreUsed.value }} core chars
        </v-chip>
        <v-btn
          size="small"
          variant="tonal"
          color="warning"
          prepend-icon="mdi-restoration"
          title="把 Prompt 模板与 Core Memory 一起恢复为内置默认"
          :disabled="everythingDefault"
          :loading="persona.busy.value && pending === 'all'"
          @click="resetAll"
        >
          恢复默认人设
        </v-btn>
      </div>
      <div class="text-caption text-disabled mb-4">
        Prompt 模板与 Core Memory 存在数据库里，仅由人工编辑；Short-term Memory 由 Neuro 自己维护。
        保存后下一轮对话立即生效，无需 reload。
      </div>

      <v-tabs v-model="tab" density="compact" class="mb-4">
        <v-tab value="prompt">Prompt</v-tab>
        <v-tab value="core">Core Memory</v-tab>
        <v-tab value="memory">Short-term Memory</v-tab>
      </v-tabs>

      <PromptEditor
        v-if="tab === 'prompt'"
        :content="persona.prompt.value.content"
        :updated-at="persona.prompt.value.updated_at"
        :is-default="persona.prompt.value.is_default"
        :busy="persona.busy.value"
        :placeholders="persona.placeholders.value"
        :api="promptApi"
        reset-label="恢复默认 Prompt"
        reset-confirm="把 Prompt 模板恢复为内置默认？当前 Prompt 内容会丢失（Core Memory 不受影响）。"
      />

      <CoreMemoryList
        v-else-if="tab === 'core'"
        :blocks="persona.blocks.value"
        :busy="persona.busy.value"
        :is-default="persona.coreIsDefault.value"
        :api="coreApi"
      />

      <ShortTermMemoryPanel v-else :active="tab === 'memory'" />
    </template>
  </div>
</template>

<script lang="ts" setup>
import { computed, ref, watch } from 'vue'
import { usePersona } from '@/composables/usePersona'
import { useModuleStatus } from '@/composables/useModuleStatus'
import ModuleOffline from '@/components/ModuleOffline.vue'
import ShortTermMemoryPanel from '@/components/ShortTermMemoryPanel.vue'
import PromptEditor from '@/components/persona/PromptEditor.vue'
import CoreMemoryList from '@/components/persona/CoreMemoryList.vue'

const persona = usePersona()
const status = useModuleStatus('neuro_sama')

const tab = ref<'prompt' | 'core' | 'memory'>('prompt')
const pending = ref<'all' | null>(null)

// Prompt 与 Core Memory 都已是默认值时，整体重置按钮无意义
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
    if (reachable) void persona.load()
  },
  { immediate: true },
)

async function resetAll() {
  if (
    !window.confirm(
      '恢复默认人设？\n\n- Prompt 模板 → 内置默认\n- Core Memory → 内置默认块\n\n当前的自定义内容会全部丢失，且无法撤销。',
    )
  )
    return
  pending.value = 'all'
  const ok = await persona.resetAll()
  pending.value = null
  if (ok) persona.error.value = ''
}
</script>

<style scoped>
.persona-page {
  max-width: 1080px;
  margin: 0 auto;
  padding: 8px 0 32px;
}
.persona-fill {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 50vh;
}
</style>
