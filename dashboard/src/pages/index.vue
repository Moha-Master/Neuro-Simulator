<template>
  <v-container>
    <v-card>
      <v-card-title class="text-h5">
        <v-icon icon="mdi-home" class="mr-2"></v-icon>
        Modules
      </v-card-title>
      <v-card-subtitle>
        Module status and lifecycle management (via vedal). Each module runs as an independent process.
      </v-card-subtitle>
      <v-card-text>
        <v-row class="mb-2">
          <v-col cols="12" md="6">
            <v-alert :color="connectionStore.isVedalConnected ? 'success' : 'error'" variant="tonal">
              <div class="d-flex align-center">
                <v-icon :icon="connectionStore.isVedalConnected ? 'mdi-check-circle' : 'mdi-alert-circle'" class="mr-2"></v-icon>
                <span>Vedal Connection: {{ connectionStore.isVedalConnected ? 'Connected' : 'Disconnected' }}</span>
              </div>
            </v-alert>
          </v-col>
          <v-col cols="12" md="6">
            <v-alert color="info" variant="tonal">
              <div class="d-flex align-center">
                <v-icon icon="mdi-clock" class="mr-2"></v-icon>
                <span>Last Health Check: {{ connectionStore.lastCheckAt || 'Not checked' }}</span>
              </div>
            </v-alert>
          </v-col>
        </v-row>

        <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
        <v-progress-linear v-if="loading" indeterminate class="mb-4"></v-progress-linear>

        <v-row v-if="moduleList.length">
          <v-col v-for="m in moduleList" :key="m.name" cols="12" md="6" lg="4">
            <v-card variant="outlined">
              <v-card-title class="d-flex align-center py-2" style="min-height: auto;">
                <v-icon
                  :icon="m.reachable ? 'mdi-check-circle' : 'mdi-stop-circle'"
                  :color="m.reachable ? 'success' : 'error'"
                  class="mr-2"
                ></v-icon>
                <span class="text-subtitle-1">{{ m.name }}</span>
                <v-spacer></v-spacer>
                <v-chip size="small" :color="m.reachable ? 'success' : 'error'" variant="tonal">
                  {{ m.reachable ? 'Running' : 'Stopped' }}
                </v-chip>
              </v-card-title>
              <v-card-text class="pb-1">
                <div class="text-caption text-disabled">{{ m.url || 'no url configured' }}</div>
                <div v-if="m.pid && m.managed" class="text-caption">
                  PID {{ m.pid }} · managed by vedal
                </div>
                <div v-else-if="m.pid" class="text-caption">
                  PID {{ m.pid }} · launched externally (manageable via PID file)
                </div>
                <div v-else-if="m.reachable" class="text-caption text-warning">
                  运行中但没有 PID 文件，vedal 无法停止它
                </div>
                <div v-else-if="m.name === 'vedal'" class="text-caption text-disabled">
                  管理模块本身，无法通过自身管理
                </div>
              </v-card-text>
              <v-card-actions class="pb-2">
                <v-btn
                  size="small"
                  color="success"
                  variant="tonal"
                  :disabled="m.name === 'vedal' || busy[m.name]"
                  @click="act(m.name, 'run')"
                >
                  <v-icon left>mdi-play</v-icon>
                  启动
                </v-btn>
                <v-btn
                  size="small"
                  color="warning"
                  variant="tonal"
                  :disabled="m.name === 'vedal' || busy[m.name]"
                  @click="act(m.name, 'restart')"
                >
                  <v-icon left>mdi-restart</v-icon>
                  重启
                </v-btn>
                <v-btn
                  size="small"
                  color="error"
                  variant="tonal"
                  :disabled="m.name === 'vedal' || busy[m.name]"
                  @click="act(m.name, 'stop')"
                >
                  <v-icon left>mdi-stop</v-icon>
                  停止
                </v-btn>
                <v-progress-circular
                  v-if="busy[m.name]"
                  indeterminate
                  size="18"
                  width="2"
                  class="ms-1"
                ></v-progress-circular>
              </v-card-actions>
            </v-card>
          </v-col>
        </v-row>
        <v-alert v-else-if="!loading" type="warning" variant="tonal">
          No modules found in config.yaml.
        </v-alert>

        <div class="d-flex justify-end mt-4">
          <v-btn color="primary" @click="loadModules" :disabled="loading">
            <v-icon left>mdi-refresh</v-icon>
            刷新
          </v-btn>
        </div>
      </v-card-text>
    </v-card>
  </v-container>
</template>

<script lang="ts" setup>
import { ref, computed, onMounted } from 'vue'
import { api, useConnectionStore } from '@/stores/connection'

interface ModuleInfo {
  url: string | null
  reachable: boolean
  managed: boolean
  pid: number | null
}

const connectionStore = useConnectionStore()

const moduleMap = ref<Record<string, ModuleInfo>>({})
const loading = ref(false)
const busy = ref<Record<string, boolean>>({})
const error = ref('')

const moduleList = computed(() =>
  Object.entries(moduleMap.value).map(([name, m]) => ({ name, ...m }))
)

const loadModules = async (silent = false) => {
  if (!silent) loading.value = true
  try {
    const data = await api<{ modules: Record<string, ModuleInfo> }>('/manage/modules')
    moduleMap.value = data.modules
    error.value = ''
  } catch (e) {
    error.value = '获取模块状态失败: ' + (e instanceof Error ? e.message : 'Unknown error')
  } finally {
    if (!silent) loading.value = false
  }
}

// 执行生命周期操作，然后轮询等待状态稳定（启动/停止过渡期数秒）
const act = async (name: string, op: 'run' | 'stop' | 'restart') => {
  try {
    busy.value = { ...busy.value, [name]: true }
    error.value = ''
    await api(`/manage/module_${op}/${name}`, { method: 'POST' })
    for (let i = 0; i < 10; i++) {
      await new Promise((res) => setTimeout(res, 1000))
      await loadModules(true)
      const m = moduleMap.value[name]
      if (op === 'stop' && !m?.reachable) break
      if ((op === 'run' || op === 'restart') && m?.reachable) break
    }
  } catch (e) {
    error.value = `${name} ${op} 失败: ` + (e instanceof Error ? e.message : 'Unknown error')
    await loadModules(true)
  } finally {
    busy.value = { ...busy.value, [name]: false }
  }
}

onMounted(() => {
  connectionStore.checkHealth()
  loadModules()
})
</script>
