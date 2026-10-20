<template>
  <div class="templates-page">
    <div v-if="!status.checked.value" class="templates-fill">
      <v-progress-circular indeterminate size="48" width="4" />
    </div>

    <ModuleOffline
      v-else-if="!status.reachable.value"
      module-name="stream"
      :on-started="status.check"
    />

    <template v-else>
      <v-alert
        v-if="store.error.value"
        type="error"
        variant="tonal"
        density="compact"
        closable
        class="mb-4"
        @click:close="store.error.value = ''"
      >
        {{ store.error.value }}
      </v-alert>

      <div class="d-flex align-center flex-wrap ga-2 mb-1">
        <div class="text-h5">Message Templates</div>
        <v-spacer />
        <v-chip size="small" :color="store.context.value.live ? 'success' : 'grey'" variant="tonal">
          {{ store.context.value.live ? '直播中' : '未开播' }}
        </v-chip>
      </div>
      <div class="text-caption text-disabled mb-4">
        stream 每轮拼给 Neuro / Chatbot 的 user message。存数据库、仅人工编辑，
        保存后下一轮生效；预览会用当前场次的真实上下文（标题 / 分区 / 场景 / 最近弹幕）渲染一次。
      </div>

      <v-card variant="tonal" class="mb-4">
        <v-card-text class="d-flex align-center flex-wrap ga-3 py-2">
          <div class="text-overline">预览上下文</div>
          <v-chip size="x-small" variant="outlined" :title="'本场标题'">
            title: {{ store.context.value.title || '—' }}
          </v-chip>
          <v-chip size="x-small" variant="outlined">
            category: {{ store.context.value.category || '—' }}
          </v-chip>
          <v-chip size="x-small" variant="outlined">scene: {{ store.context.value.scene || '—' }}</v-chip>
          <v-chip size="x-small" variant="outlined">round: {{ store.context.value.rounds }}</v-chip>
          <v-spacer />
          <v-btn size="x-small" variant="text" prepend-icon="mdi-record-circle-outline" to="/stream">
            去 Control 改本场信息
          </v-btn>
        </v-card-text>
      </v-card>

      <v-progress-linear v-if="store.isLoading.value" indeterminate class="mb-4" />

      <template v-else>
        <v-tabs v-model="tab" density="compact" class="mb-4">
          <v-tab v-for="t in store.templates.value" :key="t.name" :value="t.name">
            {{ t.label }}
          </v-tab>
        </v-tabs>

        <div
          v-for="t in store.templates.value"
          :key="t.name"
          v-show="tab === t.name"
          class="templates-page__body"
        >
          <div class="text-body-2 text-disabled mb-3">{{ t.description }}</div>
          <PromptEditor
            :content="t.content"
            :updated-at="t.updated_at"
            :is-default="t.is_default"
            :busy="store.busy.value"
            :placeholders="t.placeholders"
            :required="t.required"
            :rows="14"
            :api="templateApi(t.name)"
            preview-label="预览本轮实际消息"
            :preview-title="`渲染结果 · ${t.label}`"
            reset-label="恢复默认模板"
            :reset-confirm="`把「${t.label}」模板恢复为内置默认？当前内容会丢失。`"
            append-hint="未提供的占位符会被清空（不会把 {{xxx}} 喂给模型）；必需占位符缺失时无法保存。"
          />
        </div>
      </template>
    </template>
  </div>
</template>

<script lang="ts" setup>
import { ref, watch } from 'vue'
import { useStreamTemplates } from '@/composables/useStreamTemplates'
import { useModuleStatus } from '@/composables/useModuleStatus'
import ModuleOffline from '@/components/ModuleOffline.vue'
import PromptEditor from '@/components/persona/PromptEditor.vue'

const store = useStreamTemplates()
const status = useModuleStatus('stream')

const tab = ref('neuro_user')

function templateApi(name: string) {
  return {
    save: (content: string) => store.save(name, content),
    reset: () => store.reset(name),
    preview: (draft: string) => store.preview(name, draft),
  }
}

watch(
  () => status.reachable.value,
  (reachable) => {
    if (reachable) void store.load()
  },
  { immediate: true },
)

watch(
  () => store.templates.value,
  (list) => {
    const first = list[0]
    if (first && !list.some((t) => t.name === tab.value)) tab.value = first.name
  },
)
</script>

<style scoped>
.templates-page {
  max-width: 1080px;
  margin: 0 auto;
  padding: 8px 0 32px;
}
.templates-fill {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 50vh;
}
</style>
