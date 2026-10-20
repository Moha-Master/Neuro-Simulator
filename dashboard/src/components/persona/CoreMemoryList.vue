<template>
  <div class="core-memory">
    <div class="d-flex align-center flex-wrap ga-2 mb-2">
      <div class="text-subtitle-1">Core Memory</div>
      <v-chip size="small" variant="outlined">{{ blocks.length }} blocks</v-chip>
      <v-chip size="small" variant="tonal">{{ usedChars }} chars</v-chip>
      <v-spacer />
      <v-btn
        size="small"
        variant="text"
        color="warning"
        prepend-icon="mdi-restoration"
        title="用内置默认块替换当前全部核心记忆"
        :disabled="isDefault"
        :loading="busy && pending === 'reset'"
        @click="resetAll"
      >
        恢复默认块
      </v-btn>
      <v-btn color="primary" prepend-icon="mdi-plus" size="small" @click="openAdd">
        新增块
      </v-btn>
    </div>
    <div class="text-caption text-disabled mb-4">{{ hint }}</div>

    <v-card v-for="b in blocks" :key="b.id" class="mb-3" variant="outlined">
      <v-card-title class="d-flex align-center py-2">
        <v-icon size="small" color="primary" class="mr-2">mdi-account-details-outline</v-icon>
        <span class="text-body-1">{{ b.title }}</span>
        <v-spacer />
        <v-btn
          icon="mdi-pencil"
          size="small"
          variant="text"
          :disabled="busy"
          @click="openEdit(b)"
        />
        <v-btn
          icon="mdi-delete"
          size="small"
          variant="text"
          color="error"
          :disabled="busy"
          @click="confirmDelete(b)"
        />
      </v-card-title>
      <v-card-text class="pt-0">
        <pre class="core-memory__content">{{ b.content }}</pre>
        <div class="text-caption text-disabled mt-2">
          {{ b.content.length }} chars · updated {{ b.updated_at }}
        </div>
      </v-card-text>
    </v-card>

    <v-card v-if="!blocks.length" variant="outlined">
      <v-card-text class="text-center text-disabled py-8">
        <v-icon size="40" class="mb-2">mdi-account-details-outline</v-icon>
        <div>暂无核心记忆。点击「新增块」建立 subject（如 Persona / Vedal / Timeline）。</div>
      </v-card-text>
    </v-card>

    <v-dialog v-model="dialog" max-width="720" persistent>
      <v-card>
        <v-card-title>{{ editing ? `编辑核心记忆 · ${editing.title}` : '新增核心记忆' }}</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="title"
            label="Subject / 标题"
            variant="outlined"
            density="compact"
            hide-details
            class="mb-3"
            autofocus
          />
          <v-textarea
            v-model="content"
            label="内容（按原样渲染，可用 - 列表）"
            variant="outlined"
            rows="10"
            hide-details
            spellcheck="false"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" :disabled="busy" @click="dialog = false">取消</v-btn>
          <v-btn
            color="primary"
            :disabled="!title.trim() || !content.trim()"
            :loading="busy && pending === 'save'"
            @click="save"
          >
            保存
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="snackbar" :timeout="1800" location="bottom">{{ snackbarText }}</v-snackbar>
  </div>
</template>

<script lang="ts" setup>
import { computed, ref } from 'vue'
import type { CoreMemoryBlock } from '@/types/persona'

/** 人工维护的 core memory 块列表（neuro 人设与 chatbot 人设共用）。
 *  读写由父页面通过 api 传入；组件内部只管草稿对话框。 */
const props = withDefaults(
  defineProps<{
    blocks: CoreMemoryBlock[]
    busy?: boolean
    isDefault?: boolean
    hint?: string
    resetConfirm?: string
    api: {
      add: (title: string, content: string) => Promise<boolean>
      update: (id: number, title: string, content: string) => Promise<boolean>
      remove: (id: number) => Promise<boolean>
      reset: () => Promise<boolean>
    }
  }>(),
  {
    busy: false,
    isDefault: false,
    hint: '仅人工编辑，不向 LLM 暴露任何写工具。一条块 = 一个 subject，内容按原样渲染进 prompt。',
    resetConfirm:
      '把 Core Memory 恢复为内置默认块？\n\n当前所有块（含你自己新增的）会被替换，无法撤销。',
  },
)

const dialog = ref(false)
const editing = ref<CoreMemoryBlock | null>(null)
const title = ref('')
const content = ref('')
const pending = ref<'save' | 'delete' | 'reset' | null>(null)
const snackbar = ref(false)
const snackbarText = ref('')

const usedChars = computed(() =>
  props.blocks.reduce((sum, b) => sum + b.title.length + b.content.length, 0),
)

function notify(text: string) {
  snackbarText.value = text
  snackbar.value = true
}

function openAdd() {
  editing.value = null
  title.value = ''
  content.value = ''
  dialog.value = true
}

function openEdit(block: CoreMemoryBlock) {
  editing.value = block
  title.value = block.title
  content.value = block.content
  dialog.value = true
}

async function save() {
  if (!title.value.trim() || !content.value.trim()) return
  pending.value = 'save'
  const ok = editing.value
    ? await props.api.update(editing.value.id, title.value, content.value)
    : await props.api.add(title.value, content.value)
  pending.value = null
  if (ok) {
    dialog.value = false
    notify(editing.value ? '核心记忆已更新' : '核心记忆已新增')
  }
}

async function confirmDelete(block: CoreMemoryBlock) {
  const preview = block.content.slice(0, 200)
  if (!window.confirm(`删除核心记忆「${block.title}」？\n\n${preview}${block.content.length > 200 ? '…' : ''}`))
    return
  pending.value = 'delete'
  const ok = await props.api.remove(block.id)
  pending.value = null
  if (ok) notify('核心记忆已删除')
}

async function resetAll() {
  if (!window.confirm(props.resetConfirm)) return
  pending.value = 'reset'
  const ok = await props.api.reset()
  pending.value = null
  if (ok) notify('Core Memory 已恢复默认')
}
</script>

<style scoped>
.core-memory__content {
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  font-family: inherit;
  font-size: 0.875rem;
  line-height: 1.55;
}
</style>
