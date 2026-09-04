<template>
  <div class="neuro-sama-page">
    <!-- 首次可达性检查未完成：loading（避免误判为离线） -->
    <div v-if="!status.checked.value" class="neuro-sama-fill">
      <v-progress-circular indeterminate size="48" width="4" />
    </div>

    <!-- 模块未运行：阻止进入，提示并可就地启动 -->
    <ModuleOffline
      v-else-if="!status.reachable.value"
      module-name="neuro_sama"
      :on-started="status.check"
    />

    <!-- 模块运行中：正常 Chat 界面 -->
    <template v-else>
    <!-- Sidebar -->
    <ChatSidebar
      :sessions="conversations.sessions.value"
      :is-loading="conversations.isLoading.value"
      :selected-channel="chat.activeChannel.value"
      :selected-conversation-id="chat.activeConversationId.value"
      @select="handleSelectSession"
      @delete="handleDeleteSession"
      @rename="handleRenameSession"
      @new-chat="handleNewChat"
    />

    <!-- Main chat area -->
    <div class="neuro-sama-main">
      <!-- Errors -->
      <v-alert
        v-if="chat.error.value"
        type="error"
        variant="tonal"
        density="compact"
        closable
        class="mx-4 mt-2"
        @click:close="chat.error.value = ''"
      >
        {{ chat.error.value }}
      </v-alert>
      <v-alert
        v-if="conversations.error.value"
        type="warning"
        variant="tonal"
        density="compact"
        closable
        class="mx-4 mt-2"
        @click:close="conversations.error.value = ''"
      >
        {{ conversations.error.value }}
      </v-alert>

      <!-- Empty state -->
      <div v-if="!chat.messages.value.length && !chat.isStreaming.value" class="neuro-sama-empty">
        <v-icon size="64" color="grey-lighten-1">mdi-robot-outline</v-icon>
        <div class="text-h6 text-grey mt-4">Neuro Sama</div>
        <div class="text-body-2 text-grey-darken-1 mt-1">Start a conversation or select one from the sidebar</div>
      </div>

      <!-- Messages -->
      <div v-else ref="messagesContainer" class="neuro-sama-messages">
        <ChatMessage
          v-for="msg in chat.messages.value"
          :key="msg.key"
          :msg="msg"
        />

        <div ref="messagesEnd" />
      </div>

      <!-- Input (always visible) -->
      <ChatInput
        :is-streaming="chat.isStreaming.value"
        @send="handleSend"
        @stop="handleStop"
      />
    </div>
    </template>
  </div>
</template>

<script lang="ts" setup>
import { ref, watch, nextTick } from 'vue'
import ChatSidebar from '@/components/chat/ChatSidebar.vue'
import ChatMessage from '@/components/chat/ChatMessage.vue'
import ChatInput from '@/components/chat/ChatInput.vue'
import ModuleOffline from '@/components/ModuleOffline.vue'
import { useChat } from '@/composables/useChat'
import { useConversations } from '@/composables/useConversations'
import { useModuleStatus } from '@/composables/useModuleStatus'
import type { Conversation } from '@/types/chat'

const chat = useChat()
const conversations = useConversations()
const status = useModuleStatus('neuro_sama')

const messagesContainer = ref<HTMLElement | null>(null)
const messagesEnd = ref<HTMLElement | null>(null)

function scrollToBottom() {
  nextTick(() => {
    messagesEnd.value?.scrollIntoView({ behavior: 'smooth' })
  })
}

watch(
  () => chat.messages.value.length,
  () => scrollToBottom(),
)

watch(
  () => chat.currentContent.value,
  () => scrollToBottom(),
)

async function handleSelectSession(session: Conversation) {
  chat.reset()
  chat.activeConversationId.value = session.conversation_id
  chat.activeChannel.value = session.channel
  await chat.loadMessages(session.channel, session.conversation_id)
  scrollToBottom()
}

async function handleDeleteSession(session: Conversation) {
  if (!confirm(`Delete ${session.channel}:${session.conversation_id.slice(0, 12)}... and all its messages?`)) return
  await conversations.deleteSession(session.channel, session.conversation_id)
  if (chat.activeConversationId.value === session.conversation_id) {
    chat.reset()
  }
}

async function handleRenameSession(channel: string, conversationId: string, title: string) {
  await conversations.renameSession(channel, conversationId, title)
}

function handleNewChat() {
  if (chat.isStreaming.value) chat.stopGeneration()
  chat.reset()
}

async function handleSend(text: string) {
  const convId = chat.activeConversationId.value || undefined
  await chat.sendMessage(text, convId, chat.activeChannel.value || 'web')
  await conversations.loadSessions()
  scrollToBottom()
}

function handleStop() {
  chat.stopGeneration()
}

// 模块可达时加载会话列表；模块停止时重置聊天状态（页面已切到离线态）
watch(
  () => status.reachable.value,
  (r) => {
    if (r) conversations.loadSessions()
    else chat.reset()
  },
)
</script>

<style scoped>
.neuro-sama-page {
  display: flex;
  height: calc(100vh - 64px);
  overflow: hidden;
}
.neuro-sama-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.neuro-sama-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.neuro-sama-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px 0;
}
.neuro-sama-fill {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
