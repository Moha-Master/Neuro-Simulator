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
              :active="$route.path.startsWith('/neuro-sama') || $route.path.startsWith('/neuro-view')"
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
          <v-list-item to="/neuro-sama-persona" :active="$route.path === '/neuro-sama-persona'">
            <template #prepend>
              <v-icon>mdi-account-details</v-icon>
            </template>
            <v-list-item-title>Persona</v-list-item-title>
          </v-list-item>
          <v-list-item to="/neuro-view" :active="$route.path === '/neuro-view'">
            <template #prepend>
              <v-icon>mdi-television-play</v-icon>
            </template>
            <v-list-item-title>View</v-list-item-title>
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
          <v-list-item to="/stream-chatbot" :active="$route.path === '/stream-chatbot'">
            <template #prepend>
              <v-icon>mdi-account-group-outline</v-icon>
            </template>
            <v-list-item-title>Chatbot</v-list-item-title>
          </v-list-item>
          <v-list-item to="/stream-templates" :active="$route.path === '/stream-templates'">
            <template #prepend>
              <v-icon>mdi-message-text-outline</v-icon>
            </template>
            <v-list-item-title>Message Templates</v-list-item-title>
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
    <v-main :class="{ 'tw-main--fullbleed': route.meta.fullBleed }">
      <router-view v-if="route.meta.fullBleed" />
      <v-container v-else fluid>
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
const neuroOpen = ref(
  route.path.startsWith('/neuro-sama') || route.path.startsWith('/neuro-view'),
)
watch(
  () => route.path,
  (p) => {
    if (p.startsWith('/neuro-sama') || p.startsWith('/neuro-view')) neuroOpen.value = true
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

<style>
/* fullBleed 页面（Twitch 观看页）：让 v-main 成为固定高度容器，
   页面根节点用 flex 填满剩余空间。
   注意：不能依赖 --v-layout-top —— 该变量在 v-main 子元素上会继承到 0px。 */
.tw-main--fullbleed {
  display: flex;
  flex-direction: column;
  height: 100dvh;
  min-height: 0;
}
</style>
