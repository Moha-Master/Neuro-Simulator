<template>
  <div class="persona-page">
    <!-- 首次可达性检查未完成：loading -->
    <div v-if="!status.checked.value" class="persona-fill">
      <v-progress-circular indeterminate size="48" width="4" />
    </div>

    <!-- 模块未运行：阻止进入 -->
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

      <!-- ---------- Prompt ---------- -->
      <div v-if="tab === 'prompt'">
        <v-card variant="tonal" class="mb-4">
          <v-card-text class="text-body-2">
            <div class="font-weight-medium mb-1">占位符（点击复制）</div>
            <div class="d-flex flex-wrap ga-2 mb-2">
              <v-chip
                v-for="p in persona.placeholders.value"
                :key="p.key"
                size="small"
                variant="flat"
                color="primary"
                class="font-weight-medium"
                @click="copy(p.key)"
              >
                {{ p.key }}
              </v-chip>
            </div>
            <div
              v-for="p in persona.placeholders.value"
              :key="p.key + '-desc'"
              class="text-caption text-disabled mb-1"
            >
              <code>{{ p.key }}</code> — {{ p.description }}
            </div>
            <div class="text-caption text-disabled mt-2">
              占位符缺失时，对应段落会自动追加到 prompt 末尾（防止误删导致记忆整段丢失）。
            </div>
          </v-card-text>
        </v-card>

        <div class="d-flex align-center flex-wrap ga-2 mb-2">
          <v-chip size="small" variant="outlined">{{ draft.length }} chars</v-chip>
          <v-chip v-if="dirty" size="small" color="warning" variant="tonal">未保存</v-chip>
          <v-chip v-else size="small" color="success" variant="tonal">已保存</v-chip>
          <span v-if="persona.prompt.value.updated_at" class="text-caption text-disabled">
            updated {{ persona.prompt.value.updated_at }}
          </span>
          <v-spacer />
          <v-btn
            size="small"
            variant="text"
            prepend-icon="mdi-eye-outline"
            :loading="previewing"
            :disabled="!draft.trim()"
            @click="openPreview"
          >
            预览编译结果
          </v-btn>
        </div>

        <v-textarea
          v-model="draft"
          class="prompt-editor"
          variant="outlined"
          rows="22"
          hide-details
          spellcheck="false"
        />

        <div class="d-flex align-center flex-wrap ga-2 mt-3">
          <v-btn
            color="primary"
            :disabled="!dirty || !draft.trim()"
            :loading="persona.busy.value && pending === 'prompt'"
            @click="savePrompt"
          >
            保存
          </v-btn>
          <v-btn variant="text" :disabled="!dirty" @click="discardPrompt">放弃更改</v-btn>
          <v-spacer />
          <v-btn
            variant="text"
            color="warning"
            prepend-icon="mdi-restoration"
            :disabled="persona.prompt.value.is_default && !dirty"
            :loading="persona.busy.value && pending === 'prompt'"
            @click="resetPrompt"
          >
            恢复默认 Prompt
          </v-btn>
        </div>
      </div>

      <!-- ---------- Core Memory ---------- -->
      <div v-else-if="tab === 'core'">
        <div class="d-flex align-center flex-wrap ga-2 mb-2">
          <div class="text-subtitle-1">Core Memory</div>
          <v-chip size="small" variant="outlined">{{ persona.coreCount.value }} blocks</v-chip>
          <v-chip size="small" variant="tonal">{{ persona.coreUsed.value }} chars</v-chip>
          <v-spacer />
          <v-btn
            size="small"
            variant="text"
            color="warning"
            prepend-icon="mdi-restoration"
            title="用内置默认块替换当前全部核心记忆"
            :disabled="persona.coreIsDefault.value"
            :loading="persona.busy.value && pending === 'core-reset'"
            @click="resetCoreMemories"
          >
            恢复默认块
          </v-btn>
          <v-btn color="primary" prepend-icon="mdi-plus" size="small" @click="openAddCore">
            新增块
          </v-btn>
        </div>
        <div class="text-caption text-disabled mb-4">
          仅人工编辑，不向 LLM 暴露任何写工具。一条块 = 一个 subject（Persona / Vedal / Evil ...），
          内容按原样渲染进 prompt。
        </div>

        <v-card v-for="b in persona.blocks.value" :key="b.id" class="mb-3" variant="outlined">
          <v-card-title class="d-flex align-center py-2">
            <v-icon size="small" color="primary" class="mr-2">mdi-account-details-outline</v-icon>
            <span class="text-body-1">{{ b.title }}</span>
            <v-spacer />
            <v-btn
              icon="mdi-pencil"
              size="small"
              variant="text"
              :disabled="persona.busy.value"
              @click="openEditCore(b)"
            />
            <v-btn
              icon="mdi-delete"
              size="small"
              variant="text"
              color="error"
              :disabled="persona.busy.value"
              @click="confirmDeleteCore(b)"
            />
          </v-card-title>
          <v-card-text class="pt-0">
            <pre class="core-content">{{ b.content }}</pre>
            <div class="text-caption text-disabled mt-2">
              {{ b.content.length }} chars · updated {{ b.updated_at }}
            </div>
          </v-card-text>
        </v-card>

        <v-card v-if="!persona.blocks.value.length" variant="outlined">
          <v-card-text class="text-center text-disabled py-8">
            <v-icon size="40" class="mb-2">mdi-account-details-outline</v-icon>
            <div>暂无核心记忆。点击「新增块」建立 Persona、Vedal 等 subject。</div>
          </v-card-text>
        </v-card>
      </div>

      <!-- ---------- Short-term Memory ---------- -->
      <ShortTermMemoryPanel v-else :active="tab === 'memory'" />
    </template>

    <!-- Core Memory 新增/编辑 -->
    <v-dialog v-model="coreDialog" max-width="720" persistent>
      <v-card>
        <v-card-title>{{ editingCore ? `编辑核心记忆 · ${editingCore.title}` : '新增核心记忆' }}</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="coreTitle"
            label="Subject / 标题"
            variant="outlined"
            density="compact"
            class="mb-3"
            hide-details
            autofocus
          />
          <v-textarea
            v-model="coreContent"
            label="内容（按原样渲染，可用 - 列表）"
            variant="outlined"
            rows="10"
            hide-details
            spellcheck="false"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" :disabled="persona.busy.value" @click="coreDialog = false">取消</v-btn>
          <v-btn
            color="primary"
            :disabled="!coreTitle.trim() || !coreContent.trim()"
            :loading="persona.busy.value && pending === 'core'"
            @click="saveCore"
          >
            保存
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 编译预览 -->
    <v-dialog v-model="previewDialog" max-width="1100">
      <v-card v-if="previewData">
        <v-card-title class="d-flex align-center flex-wrap ga-2">
          <span>编译预览</span>
          <v-chip size="small" variant="outlined">{{ previewData.chars.total }} chars</v-chip>
          <v-chip size="small" variant="tonal">~{{ previewData.estimated_tokens }} tokens</v-chip>
          <v-chip size="small" variant="tonal" color="primary">
            core {{ previewData.chars.core }}
          </v-chip>
          <v-chip size="small" variant="tonal">short-term {{ previewData.chars.short_term }}</v-chip>
          <v-spacer />
          <v-btn icon="mdi-content-copy" size="small" variant="text" @click="copy(previewData.prompt)" />
        </v-card-title>
        <v-card-text>
          <pre class="prompt-preview">{{ previewData.prompt }}</pre>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="previewDialog = false">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="snackbar" :timeout="1800" location="bottom">
      {{ snackbarText }}
    </v-snackbar>
  </div>
</template>

<script lang="ts" setup>
import { ref, computed, watch } from 'vue'
import { usePersona } from '@/composables/usePersona'
import { useModuleStatus } from '@/composables/useModuleStatus'
import ModuleOffline from '@/components/ModuleOffline.vue'
import ShortTermMemoryPanel from '@/components/ShortTermMemoryPanel.vue'
import type { CoreMemoryBlock, PreviewResult } from '@/types/persona'

const persona = usePersona()
const status = useModuleStatus('neuro_sama')

const tab = ref<'prompt' | 'core' | 'memory'>('prompt')
const draft = ref('')
const pending = ref<'prompt' | 'core' | 'core-reset' | 'all' | null>(null)
const previewing = ref(false)
const previewDialog = ref(false)
const previewData = ref<PreviewResult | null>(null)

const coreDialog = ref(false)
const editingCore = ref<CoreMemoryBlock | null>(null)
const coreTitle = ref('')
const coreContent = ref('')

const snackbar = ref(false)
const snackbarText = ref('')

const dirty = computed(() => draft.value !== persona.prompt.value.content)

// Prompt 与 Core Memory 都已是默认值时，整体重置按钮无意义
const everythingDefault = computed(
  () => persona.prompt.value.is_default && !dirty.value && persona.coreIsDefault.value,
)

watch(
  () => status.reachable.value,
  (reachable) => {
    if (reachable) void persona.load()
  },
  { immediate: true },
)

// prompt 加载/保存/重置后同步编辑器
watch(
  () => persona.prompt.value.content,
  (content) => {
    draft.value = content
  },
  { immediate: true },
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

async function savePrompt() {
  if (!draft.value.trim()) return
  pending.value = 'prompt'
  const ok = await persona.savePrompt(draft.value)
  pending.value = null
  if (ok) notify('Prompt 已保存，下一轮对话生效')
}

function discardPrompt() {
  draft.value = persona.prompt.value.content
}

async function resetPrompt() {
  if (!window.confirm('把 Prompt 恢复为内置默认？当前 Prompt 内容会丢失。')) return
  pending.value = 'prompt'
  const ok = await persona.resetPrompt()
  pending.value = null
  if (ok) notify('Prompt 已恢复默认')
}

async function resetCoreMemories() {
  if (
    !window.confirm(
      '把 Core Memory 恢复为内置默认块？\n\n当前所有块（含你自己新增的）会被替换，无法撤销。',
    )
  )
    return
  pending.value = 'core-reset'
  const ok = await persona.resetCore()
  pending.value = null
  if (ok) notify('Core Memory 已恢复默认')
}

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
  if (ok) notify('Prompt 与 Core Memory 已恢复默认')
}

async function openPreview() {
  previewing.value = true
  const data = await persona.preview(draft.value)
  previewing.value = false
  if (data) {
    previewData.value = data
    previewDialog.value = true
  }
}

function openAddCore() {
  editingCore.value = null
  coreTitle.value = ''
  coreContent.value = ''
  coreDialog.value = true
}

function openEditCore(block: CoreMemoryBlock) {
  editingCore.value = block
  coreTitle.value = block.title
  coreContent.value = block.content
  coreDialog.value = true
}

async function saveCore() {
  if (!coreTitle.value.trim() || !coreContent.value.trim()) return
  pending.value = 'core'
  const ok = editingCore.value
    ? await persona.updateCore(editingCore.value.id, coreTitle.value, coreContent.value)
    : await persona.addCore(coreTitle.value, coreContent.value)
  pending.value = null
  if (ok) {
    coreDialog.value = false
    notify(editingCore.value ? '核心记忆已更新' : '核心记忆已新增')
  }
}

async function confirmDeleteCore(block: CoreMemoryBlock) {
  const ok = window.confirm(
    `删除核心记忆「${block.title}」？\n\n${block.content.slice(0, 200)}${block.content.length > 200 ? '…' : ''}`,
  )
  if (!ok) return
  const done = await persona.removeCore(block.id)
  if (done) notify('核心记忆已删除')
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
.prompt-editor :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace;
  font-size: 12.5px;
  line-height: 1.55;
}
.prompt-preview {
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
.core-content {
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  font-family: inherit;
  font-size: 0.875rem;
  line-height: 1.55;
}
</style>
