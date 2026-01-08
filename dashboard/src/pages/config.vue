<template>
  <v-container>
    <v-card>
      <v-card-title>System Configuration</v-card-title>
      <v-card-subtitle>Manage system settings for all modules</v-card-subtitle>
      <v-card-text>
        <div class="d-flex justify-end mb-4">
          <v-btn
            color="primary"
            @click="loadConfig"
            :disabled="isLoading"
            class="mr-2"
          >
            <v-icon left>mdi-refresh</v-icon>
            Refresh
          </v-btn>
          <v-btn
            color="success"
            @click="saveConfig"
            :disabled="isLoading || !config"
          >
            <v-icon left>mdi-content-save</v-icon>
            Save Configuration
          </v-btn>
        </div>

        <v-alert
          v-if="saveStatus"
          :type="saveStatusType"
          variant="tonal"
          class="mb-4"
        >
          {{ saveStatus }}
        </v-alert>

        <v-progress-linear
          v-if="isLoading"
          indeterminate
          class="mb-4"
        ></v-progress-linear>

        <div v-if="!isLoading && config">
          <!-- Dynamic tabs for config sections -->
          <v-tabs v-model="currentTab" show-arrows class="mb-4">
            <v-tab
              v-for="(value, sectionName) in config"
              :key="sectionName"
              :value="sectionName"
            >
              {{ formatTitle(String(sectionName)) }}
            </v-tab>
          </v-tabs>

          <v-window v-model="currentTab" class="pa-2">
            <v-window-item
              v-for="(sectionData, sectionName) in config"
              :key="sectionName"
              :value="sectionName"
            >
              <!-- Render each section using the ConfigSectionRenderer component -->
              <div v-for="(itemValue, itemName) in sectionData" :key="`${sectionName}.${String(itemName)}`" class="mb-3">
                <ConfigSectionRenderer
                  :key="`${sectionName}.${String(itemName)}`"
                  :item-key="String(itemName)"
                  :value="itemValue"
                  :parent-key="String(sectionName)"
                  :config-map="config"
                  @update-value="updateSectionValue(String(sectionName), String(itemName), $event)"
                />
              </div>
            </v-window-item>
          </v-window>
        </div>

        <v-alert
          v-if="!isLoading && !config"
          type="warning"
          variant="tonal"
        >
          No configuration loaded. Click "Refresh" to load configuration.
        </v-alert>
      </v-card-text>
    </v-card>
  </v-container>
</template>

<script lang="ts" setup>
import { ref, onMounted } from 'vue'
import { useConnectionStore } from '@/stores/connection'
import ConfigSectionRenderer from '@/components/ConfigSectionRenderer.vue'

const connectionStore = useConnectionStore()
const config = ref<any>(null)
const currentTab = ref<string | null>(null)
const isLoading = ref<boolean>(true)
const saveStatus = ref<string>('')
const saveStatusType = ref<'success' | 'error'>('success')

// Format section title (capitalize first letter)
const formatTitle = (title: string) => {
  return title.charAt(0).toUpperCase() + title.slice(1)
}

// Load config from backend
const loadConfig = async () => {
  if (!connectionStore.isConnected) {
    saveStatus.value = 'Not connected to Vedal Studio'
    saveStatusType.value = 'error'
    isLoading.value = false
    return
  }

  try {
    isLoading.value = true
    const configResponse = await connectionStore.sendAdminWsMessage('get_config')
    config.value = configResponse.config || configResponse

    // Set first tab as active
    const sectionNames = Object.keys(config.value || {})
    if (sectionNames.length > 0) {
      currentTab.value = sectionNames[0] || null
    }

    saveStatus.value = 'Configuration loaded successfully'
    saveStatusType.value = 'success'
  } catch (error) {
    console.error('Failed to load config:', error)
    saveStatus.value = 'Failed to load configuration: ' + (error instanceof Error ? error.message : 'Unknown error')
    saveStatusType.value = 'error'
  } finally {
    isLoading.value = false
  }
}

// Update a specific value in a section
const updateSectionValue = (sectionName: string, itemName: string, value: any) => {
  if (config.value && config.value[sectionName]) {
    config.value[sectionName][itemName] = value
  }
}

// Save config to backend
const saveConfig = async () => {
  if (!config.value) return

  try {
    // Send to backend
    const response = await connectionStore.sendAdminWsMessage('save_config', { config: config.value })
    saveStatus.value = response.message || 'Configuration saved successfully'
    saveStatusType.value = 'success'

    // Reload config after successful save to show latest content
    await loadConfig()
  } catch (error) {
    console.error('Failed to save config:', error)
    saveStatus.value = 'Failed to save configuration: ' + (error instanceof Error ? error.message : 'Invalid configuration format')
    saveStatusType.value = 'error'
  }
}

// Initialize on component mount
onMounted(() => {
  loadConfig()
})
</script>

<style scoped>
.font-mono {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
}
</style>