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
          <v-textarea
            v-model="configText"
            label="Configuration (JSON)"
            rows="20"
            auto-grow
            spellcheck="false"
            class="font-mono"
          ></v-textarea>
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

const connectionStore = useConnectionStore()
const config = ref<any>(null)
const configText = ref<string>('')
const isLoading = ref<boolean>(true)
const saveStatus = ref<string>('')
const saveStatusType = ref<'success' | 'error'>('success')

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
    configText.value = JSON.stringify(config.value, null, 2)
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

// Save config to backend
const saveConfig = async () => {
  if (!configText.value) return
  
  try {
    // Parse the JSON to validate it
    const newConfig = JSON.parse(configText.value)
    
    // Update the config object
    config.value = newConfig
    
    // Send to backend
    const response = await connectionStore.sendAdminWsMessage('save_config', { config: newConfig })
    saveStatus.value = response.message || 'Configuration saved successfully'
    saveStatusType.value = 'success'
    
    // Reload config after successful save to show latest content
    await loadConfig()
  } catch (error) {
    console.error('Failed to save config:', error)
    saveStatus.value = 'Failed to save configuration: ' + (error instanceof Error ? error.message : 'Invalid JSON format')
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