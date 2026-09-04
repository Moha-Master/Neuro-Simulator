<template>
  <v-container>
    <v-card>
      <v-card-title>Configuration Management</v-card-title>
      <v-card-subtitle>
        Edit config.yaml in plain text — one text box per schema (server / neuro_sama / vedal / ...)
      </v-card-subtitle>
      <v-card-text>
        <!-- 顶部：刷新 -->
        <div class="d-flex justify-end mb-4">
          <v-btn color="primary" @click="loadConfig" :disabled="isLoading">
            <v-icon left>mdi-refresh</v-icon>
            刷新
          </v-btn>
        </div>

        <v-alert v-if="status" :type="statusType" variant="tonal" class="mb-4">{{ status }}</v-alert>
        <v-progress-linear v-if="isLoading" indeterminate class="mb-4"></v-progress-linear>

        <div v-if="!isLoading && schemas">
          <v-tabs v-model="currentTab" show-arrows class="mb-2">
            <v-tab v-for="name in schemaNames" :key="name" :value="name">
              {{ name }}
            </v-tab>
          </v-tabs>
          <v-window v-model="currentTab" class="pa-2">
            <v-window-item v-for="name in schemaNames" :key="name" :value="name">
              <v-textarea
                v-model="schemas[name]"
                variant="outlined"
                auto-grow
                no-resize
                rows="14"
                density="compact"
                class="font-mono"
              ></v-textarea>
            </v-window-item>
          </v-window>
        </div>

        <v-alert v-if="!isLoading && !schemas" type="warning" variant="tonal">
          No configuration loaded. Click "刷新" to load configuration.
        </v-alert>

        <!-- 底部：保存并重载 / 放弃更改 / 重载所有 -->
        <div class="d-flex justify-end mt-4">
          <v-btn color="success" @click="saveAndReload" :disabled="isLoading || !schemas">
            <v-icon left>mdi-content-save</v-icon>
            保存并重载
          </v-btn>
          <v-btn color="warning" @click="discardChanges" :disabled="isLoading" class="mx-2">
            <v-icon left>mdi-undo</v-icon>
            放弃更改
          </v-btn>
          <v-btn color="error" @click="reloadAll" :disabled="isLoading">
            <v-icon left>mdi-restart</v-icon>
            重载所有
          </v-btn>
        </div>
      </v-card-text>
    </v-card>
  </v-container>
</template>

<script lang="ts" setup>
import { ref, onMounted, computed } from 'vue'
import { api, ApiError } from '@/stores/connection'

interface SaveResult {
  status: string
  changed_schemas: string[]
  reloads: Record<string, { status: string; detail?: string }>
}

const schemas = ref<Record<string, string> | null>(null)
const currentTab = ref<string | null>(null)
const isLoading = ref(false)
const status = ref('')
const statusType = ref<'success' | 'error' | 'info'>('info')

const schemaNames = computed(() => (schemas.value ? Object.keys(schemas.value) : []))

const showError = (msg: string) => {
  status.value = msg
  statusType.value = 'error'
}

const showSuccess = (msg: string) => {
  status.value = msg
  statusType.value = 'success'
}

// 获取配置文件内容（各 schema 的 YAML 原文）
const loadConfig = async () => {
  try {
    isLoading.value = true
    const data = await api<{ schemas: Record<string, string> }>('/manage/config')
    schemas.value = data.schemas
    currentTab.value = schemaNames.value[0] || null
    showSuccess('Configuration loaded')
  } catch (e) {
    showError('加载配置失败: ' + (e instanceof Error ? e.message : 'Unknown error'))
  } finally {
    isLoading.value = false
  }
}

// 保存并重载：提交全部 schema 原文，后端按实际变动的 schema 向对应模块（含 vedal 自身）发 reload
const saveAndReload = async () => {
  if (!schemas.value) return
  try {
    isLoading.value = true
    const data = await api<SaveResult>('/manage/config', {
      method: 'PUT',
      body: JSON.stringify({ schemas: schemas.value }),
    })
    const changed = data.changed_schemas
    if (changed.length === 0) {
      showSuccess('保存成功：无变动的 schema，未触发重载')
    } else {
      const reloadInfo = Object.entries(data.reloads)
        .map(([m, r]) => `${m}=${r.status}${r.detail ? ` (${r.detail})` : ''}`)
        .join(', ')
      showSuccess(`保存成功：变动 schema [${changed.join(', ')}]；重载结果: ${reloadInfo || 'none'}`)
    }
    await loadConfig()
  } catch (e) {
    showError('保存失败: ' + (e instanceof ApiError ? e.message : e instanceof Error ? e.message : 'Unknown error'))
  } finally {
    isLoading.value = false
  }
}

// 放弃更改：重新获取配置文件内容并重置文本框
const discardChanges = async () => {
  if (!confirm('放弃所有未保存的更改？')) return
  await loadConfig()
  status.value = '已放弃更改，文本框已重置为服务器当前内容'
  statusType.value = 'info'
}

// 重载所有：向所有模块和 vedal 自身执行 reload
const reloadAll = async () => {
  try {
    isLoading.value = true
    const data = await api<SaveResult>('/manage/config/reload-all', { method: 'POST' })
    const reloadInfo = Object.entries(data.reloads)
      .map(([m, r]) => `${m}=${r.status}${r.detail ? ` (${r.detail})` : ''}`)
      .join(', ')
    showSuccess(`重载完成: ${reloadInfo || 'none'}`)
  } catch (e) {
    showError('重载失败: ' + (e instanceof Error ? e.message : 'Unknown error'))
  } finally {
    isLoading.value = false
  }
}

onMounted(() => {
  loadConfig()
})
</script>

<style scoped>
.font-mono {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
}
</style>
