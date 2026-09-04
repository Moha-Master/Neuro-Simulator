<template>
  <div class="chat-sidebar">
    <div class="chat-sidebar__header pa-3">
      <v-text-field
        v-model="searchQuery"
        density="compact"
        variant="outlined"
        placeholder="Search..."
        prepend-inner-icon="mdi-magnify"
        hide-details
        clearable
        class="mb-2"
      />
      <v-btn
        block
        color="primary"
        variant="tonal"
        prepend-icon="mdi-plus"
        @click="$emit('new-chat')"
      >
        New Chat
      </v-btn>
    </div>

    <v-divider />

    <div class="chat-sidebar__list">
      <v-progress-linear v-if="isLoading" indeterminate />

      <v-list v-if="filteredSessions.length" density="compact" nav>
        <v-list-item
          v-for="session in filteredSessions"
          :key="`${session.channel}:${session.conversation_id}`"
          :active="isSelected(session)"
          :lines="isSelected(session) ? 'three' : 'two'"
          @click="$emit('select', session)"
        >
          <template #prepend>
            <v-icon
              :color="channelColor(session.channel)"
              size="small"
            >
              {{ channelIcon(session.channel) }}
            </v-icon>
          </template>

          <v-list-item-title class="text-body-2 text-truncate">
            {{ session.title || session.conversation_id.slice(0, 12) + '...' }}
          </v-list-item-title>

          <v-list-item-subtitle class="text-caption">
            <div>
              {{ session.message_count }} msgs · {{ formatRelativeTime(session.updated_at) }}
            </div>
            <div v-if="isSelected(session)" class="chat-sidebar__tools">
              {{ session.tool_calls }} calls · {{ session.tool_uses }} used
            </div>
          </v-list-item-subtitle>

          <template #append>
            <v-menu>
              <template #activator="{ props: menuProps }">
                <v-btn
                  icon="mdi-dots-vertical"
                  size="small"
                  variant="text"
                  v-bind="menuProps"
                  @click.stop
                />
              </template>
              <v-list density="compact">
                <v-list-item
                  prepend-icon="mdi-pencil"
                  title="Rename"
                  @click.stop="startRename(session)"
                />
                <v-list-item
                  prepend-icon="mdi-delete"
                  title="Delete"
                  class="text-error"
                  @click.stop="$emit('delete', session)"
                />
              </v-list>
            </v-menu>
          </template>
        </v-list-item>
      </v-list>

      <div v-else-if="!isLoading" class="text-center text-disabled py-6 text-body-2">
        No conversations yet.
      </div>
    </div>

    <!-- Rename dialog -->
    <v-dialog v-model="renameDialog" max-width="400">
      <v-card>
        <v-card-title>Rename Conversation</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="renameText"
            variant="outlined"
            density="compact"
            autofocus
            @keydown.enter="confirmRename"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="renameDialog = false">Cancel</v-btn>
          <v-btn color="primary" @click="confirmRename">Save</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script lang="ts" setup>
import { ref, computed } from 'vue'
import type { Conversation } from '@/types/chat'

const props = defineProps<{
  sessions: Conversation[]
  isLoading: boolean
  selectedChannel?: string
  selectedConversationId?: string | null
}>()

const emit = defineEmits<{
  select: [session: Conversation]
  delete: [session: Conversation]
  rename: [channel: string, conversationId: string, title: string]
  'new-chat': []
}>()

const searchQuery = ref('')
const renameDialog = ref(false)
const renameText = ref('')
const renamingSession = ref<Conversation | null>(null)

const filteredSessions = computed(() => {
  if (!searchQuery.value) return props.sessions
  const q = searchQuery.value.toLowerCase()
  return props.sessions.filter(
    (s) =>
      (s.title && s.title.toLowerCase().includes(q)) ||
      s.conversation_id.toLowerCase().includes(q) ||
      s.channel.toLowerCase().includes(q),
  )
})

function isSelected(session: Conversation): boolean {
  return session.channel === props.selectedChannel && session.conversation_id === props.selectedConversationId
}

function channelColor(channel: string): string {
  const map: Record<string, string> = { web: 'blue', qq: 'purple', wechat: 'green' }
  return map[channel] || 'grey'
}

function channelIcon(channel: string): string {
  const map: Record<string, string> = { web: 'mdi-web', qq: 'mdi-qqchat', wechat: 'mdi-wechat' }
  return map[channel] || 'mdi-message-text'
}

function formatRelativeTime(ts: string): string {
  try {
    const d = new Date(ts)
    const now = new Date()
    const diff = now.getTime() - d.getTime()
    if (diff < 60000) return 'just now'
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`
    return d.toLocaleDateString()
  } catch {
    return ts
  }
}

function startRename(session: Conversation) {
  renamingSession.value = session
  renameText.value = session.title || ''
  renameDialog.value = true
}

function confirmRename() {
  if (renamingSession.value && renameText.value.trim()) {
    emit('rename', renamingSession.value.channel, renamingSession.value.conversation_id, renameText.value.trim())
  }
  renameDialog.value = false
}
</script>

<style scoped>
.chat-sidebar {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: rgb(var(--v-theme-surface));
  border-right: 1px solid rgba(0, 0, 0, 0.08);
}
.chat-sidebar__header {
  flex-shrink: 0;
}
.chat-sidebar__list {
  flex: 1;
  overflow-y: auto;
}
.chat-sidebar__tools {
  margin-top: 2px;
  font-size: 0.7rem;
  color: rgb(var(--v-theme-on-surface) / 0.45);
  letter-spacing: 0.02em;
}
</style>
