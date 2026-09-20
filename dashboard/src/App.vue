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
        <v-list-group v-model="neuroOpen" value="neuro-sama">
          <template #activator="{ props }">
            <v-list-item
              v-bind="props"
              :active="$route.path.startsWith('/neuro-sama')"
            >
              <template #prepend>
                <v-icon>mdi-brain</v-icon>
              </template>
              <v-list-item-title>Neuro Sama</v-list-item-title>
            </v-list-item>
          </template>
          <v-list-item to="/neuro-sama" :active="$route.path === '/neuro-sama'">
            <template #prepend>
              <v-icon>mdi-message-processing</v-icon>
            </template>
            <v-list-item-title>Chat</v-list-item-title>
          </v-list-item>
          <v-list-item to="/neuro-sama-memory" :active="$route.path === '/neuro-sama-memory'">
            <template #prepend>
              <v-icon>mdi-database</v-icon>
            </template>
            <v-list-item-title>Memory</v-list-item-title>
          </v-list-item>
        </v-list-group>
        <v-list-group v-model="streamOpen" value="stream">
          <template #activator="{ props }">
            <v-list-item
              v-bind="props"
              :active="$route.path.startsWith('/stream')"
            >
              <template #prepend>
                <v-icon>mdi-broadcast</v-icon>
              </template>
              <v-list-item-title>Stream</v-list-item-title>
            </v-list-item>
          </template>
          <v-list-item to="/stream" :active="$route.path === '/stream'">
            <template #prepend>
              <v-icon>mdi-record-circle-outline</v-icon>
            </template>
            <v-list-item-title>Control</v-list-item-title>
          </v-list-item>
          <v-list-item to="/stream-view" :active="$route.path === '/stream-view'">
            <template #prepend>
              <v-icon>mdi-television-classic</v-icon>
            </template>
            <v-list-item-title>View</v-list-item-title>
          </v-list-item>
        </v-list-group>
      </v-list>
    </v-navigation-drawer>

    <!-- App Bar -->
    <v-app-bar>
      <v-app-bar-nav-icon @click="drawer = !drawer"></v-app-bar-nav-icon>
      <v-app-bar-title>Vedal Studio Dashboard</v-app-bar-title>

      <!-- Connection status indicator (dashboard <-> vedal only) -->
      <template #append>
        <v-badge
          :color="connectionStore.isVedalConnected ? 'success' : 'error'"
          :model-value="true"
          dot
        >
          <v-icon>{{ connectionStore.isVedalConnected ? 'mdi-connection' : 'mdi-connection-off' }}</v-icon>
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
import { ref, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useConnectionStore } from '@/stores/connection'

const drawer = ref(false)
const route = useRoute()
const connectionStore = useConnectionStore()

// Check vedal health on app initialization (dashboard only talks to vedal)
onMounted(() => {
  console.log('App mounted, checking vedal health...')
  connectionStore.checkHealth()
})

// Neuro Sama 侧边栏分组：进入其路由时自动展开（仍可手动收起）
const neuroOpen = ref(route.path.startsWith('/neuro-sama'))
watch(
  () => route.path,
  (p) => {
    if (p.startsWith('/neuro-sama')) neuroOpen.value = true
  },
)

// Stream 侧边栏分组：同上（注意 /stream-view 也以 /stream 开头）
const streamOpen = ref(route.path.startsWith('/stream'))
watch(
  () => route.path,
  (p) => {
    if (p.startsWith('/stream')) streamOpen.value = true
  },
)
</script>
