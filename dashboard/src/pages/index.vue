<template>
  <v-container>
    <v-row>
      <v-col cols="12">
        <v-card>
          <v-card-title class="text-h5">
            <v-icon icon="mdi-home" class="mr-2"></v-icon>
            Dashboard Home
          </v-card-title>
          <v-card-subtitle>Welcome to Vedal Studio Dashboard</v-card-subtitle>
          <v-card-text>
            <v-row>
              <v-col cols="12" md="6">
                <v-alert :color="connectionStore.isConnected ? 'success' : 'error'" variant="tonal">
                  <div class="d-flex align-center">
                    <v-icon :icon="connectionStore.isConnected ? 'mdi-check-circle' : 'mdi-alert-circle'" class="mr-2"></v-icon>
                    <span>System Status: {{ connectionStore.isConnected ? 'Connected' : 'Disconnected' }}</span>
                  </div>
                </v-alert>
              </v-col>
              <v-col cols="12" md="6">
                <v-alert color="info" variant="tonal">
                  <div class="d-flex align-center">
                    <v-icon icon="mdi-clock" class="mr-2"></v-icon>
                    <span>Connection Established: {{ connectionTime || 'Not connected' }}</span>
                  </div>
                </v-alert>
              </v-col>
            </v-row>

            <v-row class="mt-4">
              <v-col cols="12">
                <h3 class="text-h6 mb-2">System Information</h3>
                <p>Vedal Studio is a centralized management system for Neuro-Simulator modules.</p>
                <p>From here you can manage configurations, monitor system status, and control various modules.</p>

                <div class="mt-4">
                  <h4 class="text-h7">Connection Details:</h4>
                  <v-list>
                    <v-list-item>
                      <v-list-item-title>Vedal Studio Connection</v-list-item-title>
                      <v-list-item-subtitle>
                        <v-chip :color="vedalStudioColor" size="small">
                          {{ vedalStudioStatus }}
                        </v-chip>
                      </v-list-item-subtitle>
                    </v-list-item>
                    <v-list-item>
                      <v-list-item-title>Neuro Sama Connection</v-list-item-title>
                      <v-list-item-subtitle>
                        <v-chip :color="neuroSamaColor" size="small">
                          {{ neuroSamaStatus }}
                        </v-chip>
                      </v-list-item-subtitle>
                    </v-list-item>
                  </v-list>
                </div>
              </v-col>
            </v-row>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<script lang="ts" setup>
import { ref, onMounted, onUnmounted, onBeforeUnmount, computed } from 'vue'
import { useConnectionStore } from '@/stores/connection'

const connectionStore = useConnectionStore()
const connectionTime = ref<string | null>(null)
const windowLocation = window.location

// Format connection time
const formatTime = (date: Date) => {
  return date.toLocaleString()
}

// Update connection time when connection status changes
connectionStore.$subscribe((mutation, state) => {
  if (state.isConnected) {
    connectionTime.value = formatTime(new Date())
  }
})

// Initialize connection on component mount
onMounted(() => {
  connectionStore.connectToVedal()
})

// Clean up on component unmount
onBeforeUnmount(() => {
  // Don't disconnect when component unmounts, as other components might need the connection
  // connectionStore.disconnect()
})

// Computed properties for connection status
const vedalStudioStatus = computed(() => connectionStore.isConnected ? 'Connected' : 'Disconnected')
const vedalStudioColor = computed(() => connectionStore.isConnected ? 'success' : 'error')

const neuroSamaStatus = computed(() => connectionStore.isNeuroSamaConnected ? 'Connected' : 'Disconnected')
const neuroSamaColor = computed(() => connectionStore.isNeuroSamaConnected ? 'success' : 'error')
</script>