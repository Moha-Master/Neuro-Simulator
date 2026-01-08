<template>
  <v-container>
    <v-card>
      <v-card-title>Neuro Sama Management</v-card-title>
      <v-card-subtitle>View and manage Neuro Sama context</v-card-subtitle>
      <v-card-text>
        <!-- Connection status indicator -->
        <v-alert
          :type="connectionStore.isNeuroSamaConnected ? 'success' : 'error'"
          variant="tonal"
          class="mb-4"
        >
          <div class="d-flex align-center">
            <v-icon :icon="connectionStore.isNeuroSamaConnected ? 'mdi-check-circle' : 'mdi-alert-circle'" class="mr-2"></v-icon>
            <span>Neuro Sama Connection: {{ connectionStore.isNeuroSamaConnected ? 'Connected' : 'Disconnected' }}</span>
          </div>
        </v-alert>

        <!-- Tabs for navigation -->
        <v-tabs v-model="currentTab" show-arrows class="mb-4">
          <v-tab value="context">
            Context
          </v-tab>
          <v-tab value="memory">
            Memory
          </v-tab>
        </v-tabs>

        <v-window v-model="currentTab" class="pa-2">
          <v-window-item value="context">
            <v-card outlined>
              <v-card-title>System Prompt</v-card-title>
              <v-card-text>
                <pre class="font-mono">{{ systemPrompt }}</pre>
              </v-card-text>
            </v-card>

            <v-card outlined class="mt-4">
              <v-card-title>Current Context</v-card-title>
              <v-card-text>
                <pre class="font-mono">{{ currentContext }}</pre>
              </v-card-text>
            </v-card>

            <div class="d-flex justify-end mt-4">
              <v-btn
                color="primary"
                @click="refreshContext"
                :disabled="isLoading"
              >
                <v-icon left>mdi-refresh</v-icon>
                Refresh
              </v-btn>
            </div>
          </v-window-item>

          <v-window-item value="memory">
            <v-card outlined>
              <v-card-title>Init Memory</v-card-title>
              <v-card-text>
                <v-textarea
                  v-model="initMemory"
                  auto-grow
                  rows="6"
                  class="font-mono"
                  :readonly="!canEditMemory"
                ></v-textarea>
              </v-card-text>
            </v-card>

            <v-card outlined class="mt-4">
              <v-card-title>Core Memory</v-card-title>
              <v-card-text>
                <v-textarea
                  v-model="coreMemory"
                  auto-grow
                  rows="10"
                  class="font-mono"
                  :readonly="!canEditMemory"
                ></v-textarea>
              </v-card-text>
            </v-card>

            <v-card outlined class="mt-4">
              <v-card-title>Temp Memory</v-card-title>
              <v-card-text>
                <v-textarea
                  v-model="tempMemory"
                  auto-grow
                  rows="6"
                  class="font-mono"
                  :readonly="!canEditMemory"
                ></v-textarea>
              </v-card-text>
            </v-card>

            <div class="d-flex justify-end mt-4">
              <v-btn
                color="primary"
                @click="refreshMemory"
                :disabled="isLoading"
                class="mr-2"
              >
                <v-icon left>mdi-refresh</v-icon>
                Refresh
              </v-btn>
              <v-btn
                color="success"
                @click="saveMemory"
                :disabled="isLoading || !canEditMemory"
              >
                <v-icon left>mdi-content-save</v-icon>
                Save
              </v-btn>
            </div>
          </v-window-item>
        </v-window>
      </v-card-text>
    </v-card>
  </v-container>
</template>

<script lang="ts" setup>
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { useConnectionStore } from '@/stores/connection'

const connectionStore = useConnectionStore()
const currentTab = ref('context')
const systemPrompt = ref('')
const currentContext = ref('')
const initMemory = ref('')
const coreMemory = ref('')
const tempMemory = ref('')
const isLoading = ref(false)
const canEditMemory = computed(() => connectionStore.isNeuroSamaConnected)

// Load initial data if connected
const loadData = async () => {
  if (connectionStore.isNeuroSamaConnected) {
    try {
      if (currentTab.value === 'context') {
        // Request initial context data
        await connectionStore.sendNeuroSamaWsMessage('get_context', {})
      } else if (currentTab.value === 'memory') {
        // Request initial memory data
        await connectionStore.sendNeuroSamaWsMessage('get_memory', {})
      }
    } catch (error) {
      console.error('Failed to load initial data:', error)
    }
  }
}

// 监听标签页变化，切换到Memory标签页时自动获取数据
watch(currentTab, async (newTab) => {
  if (newTab === 'memory' && connectionStore.isNeuroSamaConnected) {
    try {
      isLoading.value = true
      await connectionStore.sendNeuroSamaWsMessage('get_memory', {})
    } catch (error) {
      console.error('Failed to load memory data:', error)
    } finally {
      isLoading.value = false
    }
  }
})

// Refresh context data
const refreshContext = async () => {
  if (connectionStore.isNeuroSamaConnected) {
    try {
      isLoading.value = true
      await connectionStore.sendNeuroSamaWsMessage('get_context', {})
    } catch (error) {
      console.error('Failed to refresh context:', error)
    } finally {
      isLoading.value = false
    }
  }
}

// Refresh memory data
const refreshMemory = async () => {
  if (connectionStore.isNeuroSamaConnected) {
    try {
      isLoading.value = true
      await connectionStore.sendNeuroSamaWsMessage('get_memory', {})
    } catch (error) {
      console.error('Failed to refresh memory:', error)
    } finally {
      isLoading.value = false
    }
  }
}

// 监听标签页变化，切换到标签页时自动获取数据
watch(currentTab, async (newTab) => {
  if (connectionStore.isNeuroSamaConnected) {
    try {
      isLoading.value = true
      if (newTab === 'memory') {
        await connectionStore.sendNeuroSamaWsMessage('get_memory', {})
      } else if (newTab === 'context') {
        await connectionStore.sendNeuroSamaWsMessage('get_context', {})
      }
    } catch (error) {
      console.error(`Failed to load ${newTab} data:`, error)
    } finally {
      isLoading.value = false
    }
  }
})

// Save memory data
const saveMemory = async () => {
  if (connectionStore.isNeuroSamaConnected) {
    try {
      isLoading.value = true
      const memoryData = {
        init_memory: JSON.parse(initMemory.value),
        core_memory: JSON.parse(coreMemory.value),
        temp_memory: JSON.parse(tempMemory.value)
      }

      await connectionStore.sendNeuroSamaWsMessage('update_memory', { memory: memoryData })
    } catch (error) {
      console.error('Failed to save memory:', error)
      alert('Failed to save memory: ' + (error instanceof Error ? error.message : 'Invalid JSON format'))
    } finally {
      isLoading.value = false
    }
  }
}

// Handle incoming messages from Neuro Sama
const handleMessage = (event: MessageEvent) => {
  try {
    const data = JSON.parse(event.data)

    if (data.type === 'context_update') {
      systemPrompt.value = data.payload.system_prompt || ''
      currentContext.value = data.payload.current_context || ''
    } else if (data.type === 'memory_update') {
      // Convert objects to JSON strings for display in textareas
      initMemory.value = JSON.stringify(data.payload.init_memory || {}, null, 2)
      coreMemory.value = JSON.stringify(data.payload.core_memory || {}, null, 2)
      tempMemory.value = JSON.stringify(data.payload.temp_memory || {}, null, 2)
    }
  } catch (error) {
    console.error('Error parsing Neuro Sama message:', error)
  }
}

onMounted(() => {
  // Add event listener for Neuro Sama WebSocket messages
  if (connectionStore.neuroSamaWs) {
    connectionStore.neuroSamaWs.addEventListener('message', handleMessage)
  }

  // Load initial data
  loadData()
})

onUnmounted(() => {
  // Remove event listener
  if (connectionStore.neuroSamaWs) {
    connectionStore.neuroSamaWs.removeEventListener('message', handleMessage)
  }
})
</script>

<style scoped>
.font-mono {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>