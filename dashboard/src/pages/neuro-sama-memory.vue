<template>
  <div class="memory-page">
    <!-- 首次可达性检查未完成：loading -->
    <div v-if="!status.checked.value" class="memory-loading">
      <v-progress-circular indeterminate size="48" width="4" />
    </div>

    <!-- 模块未运行：阻止进入 -->
    <ModuleOffline
      v-else-if="!status.reachable.value"
      module-name="neuro_sama"
      :on-started="status.check"
    />

    <template v-else>
    <!-- Error -->
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

    <!-- Header -->
    <div class="d-flex align-center mb-2">
      <div class="text-h5">Memory</div>
      <v-spacer />
      <v-chip size="small" variant="outlined" class="mr-2">
        {{ mem.count.value }} entries
      </v-chip>
      <v-chip size="small" :color="usageColor" variant="tonal">
        {{ mem.used.value }}/{{ mem.charLimit.value }} chars
      </v-chip>
    </div>

    <!-- Usage -->
    <v-progress-linear
      :value="usagePercent"
      :color="usageColor"
      height="6"
      rounded
      class="mb-6"
    />

    <!-- Add -->
    <v-card class="mb-6">
      <v-card-text>
        <div class="d-flex gap-2 align-start">
          <v-textarea
            v-model="newContent"
            rows="1"
            auto-grow
            variant="outlined"
            density="compact"
            hide-details
            placeholder="Add a persistent memory (one declarative sentence)..."
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

    <!-- List -->
    <v-progress-linear v-if="mem.isLoading.value" indeterminate />

    <v-card v-else-if="mem.memories.value.length">
      <v-list lines="two">
        <v-list-item
          v-for="m in mem.memories.value"
          :key="m.id"
          class="memory-item"
        >
          <template #prepend>
            <v-icon color="primary" size="small">mdi-brain</v-icon>
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
        <v-icon size="40" class="mb-2">mdi-brain-off</v-icon>
        <div>暂无记忆。Neuro Sama 会通过对话自动积累，你也可以在上方手动添加。</div>
      </v-card-text>
    </v-card>

    <!-- Edit dialog -->
    <v-dialog v-model="editDialog" max-width="560">
      <v-card v-if="editing">
        <v-card-title>编辑记忆 #{{ editing.id }}</v-card-title>
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
    </template>
  </div>
</template>

<script lang="ts" setup>
import { ref, computed, watch } from 'vue'
import { useMemories } from '@/composables/useMemories'
import { useModuleStatus } from '@/composables/useModuleStatus'
import ModuleOffline from '@/components/ModuleOffline.vue'
import type { Memory } from '@/types/memory'

const mem = useMemories()
const status = useModuleStatus('neuro_sama')

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

// 模块可达时加载记忆
watch(
  () => status.reachable.value,
  (r) => {
    if (r) mem.load()
  },
)
</script>

<style scoped>
.memory-page {
  max-width: 860px;
  margin: 0 auto;
  padding: 8px 0 32px;
}
.memory-item__content {
  white-space: pre-wrap;
  word-break: break-word;
}
.memory-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 50vh;
}
</style>
