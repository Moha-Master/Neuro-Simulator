<template>
  <div class="short-term-panel">
    <v-alert
      v-if="mem.error.value"
      type="error"
      variant="tonal"
      density="compact"
      closable
      class="mb-4"
      @click:close="mem.error.value = ''"
    >
      {{ mem.error.value }}
    </v-alert>

    <div class="d-flex align-center flex-wrap ga-2 mb-1">
      <div class="text-h6">Short-term Memory</div>
      <v-spacer />
      <v-chip size="small" variant="outlined">{{ mem.count.value }} entries</v-chip>
      <v-chip size="small" :color="usageColor" variant="tonal">
        {{ mem.used.value }}/{{ mem.charLimit.value }} chars
      </v-chip>
    </div>
    <div class="text-caption text-disabled mb-3">
      Neuro 通过 <code>memory_add</code> 自主维护；这里的修改会同步到内存缓存，下一轮对话立即生效。
    </div>

    <v-progress-linear :value="usagePercent" :color="usageColor" height="6" rounded class="mb-6" />

    <v-card class="mb-6">
      <v-card-text>
        <div class="d-flex ga-2 align-start">
          <v-textarea
            v-model="newContent"
            rows="1"
            auto-grow
            variant="outlined"
            density="compact"
            hide-details
            placeholder="Add a short-term memory (one declarative sentence)..."
            :disabled="mem.busy.value"
            class="flex-grow-1"
            @keydown.enter.exact.prevent="onAdd"
          />
          <v-btn
            color="primary"
            variant="tonal"
            prepend-icon="mdi-plus"
            :disabled="!newContent.trim() || mem.busy.value"
            :loading="mem.busy.value && pending === 'add'"
            @click="onAdd"
          >
            添加
          </v-btn>
        </div>
      </v-card-text>
    </v-card>

    <v-progress-linear v-if="mem.isLoading.value" indeterminate />

    <v-card v-else-if="mem.memories.value.length">
      <v-list lines="two">
        <v-list-item v-for="m in mem.memories.value" :key="m.id" class="memory-item">
          <template #prepend>
            <v-icon color="primary" size="small">mdi-note-text-outline</v-icon>
          </template>
          <v-list-item-title class="text-body-2 memory-item__content">
            {{ m.content }}
          </v-list-item-title>
          <v-list-item-subtitle class="text-caption text-disabled">
            #{{ m.id }} · {{ m.ts }}
          </v-list-item-subtitle>
          <template #append>
            <v-btn
              icon="mdi-pencil"
              size="small"
              variant="text"
              :disabled="mem.busy.value"
              @click="startEdit(m)"
            />
            <v-btn
              icon="mdi-delete"
              size="small"
              variant="text"
              color="error"
              :disabled="mem.busy.value"
              @click="confirmDelete(m)"
            />
          </template>
        </v-list-item>
      </v-list>
    </v-card>

    <v-card v-else>
      <v-card-text class="text-center text-disabled py-8">
        <v-icon size="40" class="mb-2">mdi-note-off-outline</v-icon>
        <div>暂无短期记忆。Neuro 会在对话中通过 memory_add 自动积累，你也可以在上方手动添加。</div>
      </v-card-text>
    </v-card>

    <v-dialog v-model="editDialog" max-width="560">
      <v-card v-if="editing">
        <v-card-title>编辑短期记忆 #{{ editing.id }}</v-card-title>
        <v-card-text>
          <v-textarea
            v-model="editText"
            rows="2"
            auto-grow
            variant="outlined"
            density="compact"
            hide-details
            autofocus
            @keydown.enter.exact.meta.prevent="saveEdit"
            @keydown.enter.exact.ctrl.prevent="saveEdit"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="editDialog = false">取消</v-btn>
          <v-btn
            color="primary"
            :disabled="!editText.trim()"
            :loading="mem.busy.value && pending === 'update'"
            @click="saveEdit"
          >
            保存
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script lang="ts" setup>
import { ref, computed, watch } from 'vue'
import { useMemories } from '@/composables/useMemories'
import type { Memory } from '@/types/memory'

const props = defineProps<{ active: boolean }>()

const mem = useMemories()

const newContent = ref('')
const editDialog = ref(false)
const editing = ref<Memory | null>(null)
const editText = ref('')
const pending = ref<'add' | 'update' | 'delete' | null>(null)

const usagePercent = computed(() => {
  if (!mem.charLimit.value) return 0
  return Math.min(100, Math.round((mem.used.value / mem.charLimit.value) * 100))
})

const usageColor = computed(() => {
  const p = usagePercent.value
  if (p >= 90) return 'error'
  if (p >= 70) return 'warning'
  return 'success'
})

async function onAdd() {
  const text = newContent.value.trim()
  if (!text) return
  pending.value = 'add'
  const ok = await mem.add(text)
  pending.value = null
  if (ok) newContent.value = ''
}

function startEdit(m: Memory) {
  editing.value = m
  editText.value = m.content
  editDialog.value = true
}

async function saveEdit() {
  if (!editing.value) return
  pending.value = 'update'
  const ok = await mem.update(editing.value.id, editText.value)
  pending.value = null
  if (ok) editDialog.value = false
}

async function confirmDelete(m: Memory) {
  const ok = window.confirm(`删除记忆 #${m.id}？\n\n"${m.content}"`)
  if (!ok) return
  pending.value = 'delete'
  await mem.remove(m.id)
  pending.value = null
}

// 切到该 tab 时加载/刷新（Persona 页面保证模块已可达）
watch(
  () => props.active,
  (active) => {
    if (active) mem.load()
  },
  { immediate: true },
)
</script>

<style scoped>
.memory-item__content {
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
