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
        <v-list-item to="/state" :active="$route.path === '/state'">
          <template #prepend>
            <v-icon>mdi-sitemap-outline</v-icon>
          </template>
          <v-list-item-title>State</v-list-item-title>
        </v-list-item>
        <v-list-item to="/config" :active="$route.path === '/config'">
          <template #prepend>
            <v-icon>mdi-cog</v-icon>
          </template>
          <v-list-item-title>Configuration</v-list-item-title>
        </v-list-item>
        <v-list-item to="/logs" :active="$route.path === '/logs'">
          <template #prepend>
            <v-icon>mdi-file-document-outline</v-icon>
          </template>
          <v-list-item-title>Logs</v-list-item-title>
        </v-list-item>
        <v-list-group v-model="neuroOpen" value="neuro-sama">
          <template #activator="{ props }">
            <v-list-item
              v-bind="props"
              :active="$route.path.startsWith('/neuro-sama') || $route.path.startsWith('/neuro-view') || $route.path.startsWith('/neuro-tools') || $route.path.startsWith('/logs-neuro')"
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
          <v-list-item to="/neuro-tools" :active="$route.path === '/neuro-tools'">
            <template #prepend>
              <v-icon>mdi-tools</v-icon>
            </template>
            <v-list-item-title>Tools</v-list-item-title>
          </v-list-item>
          <v-list-item to="/logs-neuro" :active="$route.path === '/logs-neuro'">
            <template #prepend>
              <v-icon>mdi-text-box-search-outline</v-icon>
            </template>
            <v-list-item-title>Logs</v-list-item-title>
          </v-list-item>
        </v-list-group>
        <v-list-group v-model="streamOpen" value="stream">
          <template #activator="{ props }">
            <v-list-item
              v-bind="props"
              :active="$route.path.startsWith('/stream') || $route.path.startsWith('/logs-stream')"
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
            <v-list-item-title>Studio</v-list-item-title>
          </v-list-item>
          <v-list-item to="/stream-sessions" :active="$route.path === '/stream-sessions'">
            <template #prepend>
              <v-icon>mdi-history</v-icon>
            </template>
            <v-list-item-title>Sessions</v-list-item-title>
          </v-list-item>
          <v-list-item
            href="/manage/stream/view/"
            target="_blank"
            rel="noopener"
          >
            <template #prepend>
              <v-icon>mdi-open-in-new</v-icon>
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
          <v-list-item to="/logs-stream" :active="$route.path === '/logs-stream'">
            <template #prepend>
              <v-icon>mdi-text-box-search-outline</v-icon>
            </template>
            <v-list-item-title>Logs</v-list-item-title>
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

    <!-- 单 key 认证：任一受保护 API 返回 401 → 此 Vedal 已启用 auth_key。
         登录成功后只写会话 cookie（key 不落 localStorage），随后整页重载。 -->
    <v-dialog :model-value="connectionStore.authRequired" persistent max-width="440">
      <v-card title="此 Vedal 已启用鉴权">
        <v-card-text>
          <p class="text-body-2 mb-4">
            请输入 <code>vedal.auth_key</code>。登录成功后会话 cookie 会自动随所有请求
            （含 EventSource）发送。
          </p>
          <v-text-field
            v-model="loginKey"
            label="Auth key"
            type="password"
            variant="outlined"
            density="compact"
            autofocus
            @keyup.enter="submitLogin"
          />
          <v-alert
            v-if="loginError"
            type="error"
            variant="tonal"
            density="compact"
            class="mt-2"
          >
            {{ loginError }}
          </v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn
            color="primary"
            :loading="loginBusy"
            :disabled="!loginKey"
            @click="submitLogin"
          >
            登录
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-app>
</template>

<script lang="ts" setup>
import { ref, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, useConnectionStore } from '@/stores/connection'

const drawer = ref(false)
const route = useRoute()
const connectionStore = useConnectionStore()

// ---------- 单 key 认证登录遮罩 ----------
const loginKey = ref('')
const loginBusy = ref(false)
const loginError = ref('')

async function submitLogin() {
  if (!loginKey.value) return
  loginBusy.value = true
  loginError.value = ''
  try {
    await api('/login', { method: 'POST', body: JSON.stringify({ key: loginKey.value }) })
    // key 不写 localStorage：cookie 就是会话。重载让页面从已认证状态重新拉数据。
    window.location.reload()
  } catch {
    loginError.value = 'key 不正确或登录失败，请重试'
    loginBusy.value = false
  }
}

// Check vedal health on app initialization (dashboard only talks to vedal)
onMounted(() => {
  console.log('App mounted, checking vedal health...')
  connectionStore.checkHealth()
})

// Neuro Sama 侧边栏分组：进入其路由时自动展开（仍可手动收起）
const neuroOpen = ref(
  route.path.startsWith('/neuro-sama') ||
    route.path.startsWith('/neuro-view') ||
    route.path.startsWith('/neuro-tools') ||
    route.path.startsWith('/logs-neuro'),
)
watch(
  () => route.path,
  (p) => {
    if (
      p.startsWith('/neuro-sama') ||
      p.startsWith('/neuro-view') ||
      p.startsWith('/neuro-tools') ||
      p.startsWith('/logs-neuro')
    ) {
      neuroOpen.value = true
    }
  },
)

// Stream 侧边栏分组：同上（/stream-sessions 也以 /stream 开头）
const streamOpen = ref(
  route.path.startsWith('/stream') || route.path.startsWith('/logs-stream'),
)
watch(
  () => route.path,
  (p) => {
    if (p.startsWith('/stream') || p.startsWith('/logs-stream')) streamOpen.value = true
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
