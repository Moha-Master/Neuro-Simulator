<template>
  <v-app>
    <!-- Navigation Drawer -->
    <v-navigation-drawer v-model="drawer" temporary>
      <v-list nav>
        <v-list-item to="/" :active="$route.path === '/'">
          <template #prepend>
            <v-icon>mdi-home</v-icon>
          </template>
          <v-list-item-title>Home</v-list-item-title>
        </v-list-item>
        <v-list-item to="/config" :active="$route.path === '/config'">
          <template #prepend>
            <v-icon>mdi-cog</v-icon>
          </template>
          <v-list-item-title>Configuration</v-list-item-title>
        </v-list-item>
      </v-list>
    </v-navigation-drawer>

    <!-- App Bar -->
    <v-app-bar>
      <v-app-bar-nav-icon @click="drawer = !drawer"></v-app-bar-nav-icon>
      <v-app-bar-title>Vedal Studio Dashboard</v-app-bar-title>

      <!-- Connection status indicator -->
      <template #append>
        <v-badge
          :color="connectionStore.isConnected ? 'success' : 'error'"
          :model-value="true"
          dot
        >
          <v-icon>{{ connectionStore.isConnected ? 'mdi-connection' : 'mdi-connection-off' }}</v-icon>
        </v-badge>
      </template>
    </v-app-bar>

    <!-- Main Content -->
    <v-main>
      <v-container fluid>
        <router-view />
      </v-container>
    </v-main>
  </v-app>
</template>

<script lang="ts" setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useConnectionStore } from '@/stores/connection'

const drawer = ref(false)
const route = useRoute()
const connectionStore = useConnectionStore()

// Computed property to get current route
const currentRoute = computed(() => route.path)

// Connect to WebSocket on app initialization
onMounted(() => {
  console.log('App mounted, connecting to WebSocket...')
  connectionStore.connect()
})
</script>
