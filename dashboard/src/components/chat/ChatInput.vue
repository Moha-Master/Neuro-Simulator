<template>
  <div class="chat-input">
    <v-textarea
      ref="textareaRef"
      v-model="inputText"
      :rows="1"
      :max-rows="10"
      auto-grow
      variant="outlined"
      density="compact"
      placeholder="Type a message..."
      :disabled="disabled"
      hide-details
      class="chat-input__textarea"
      @keydown.enter.exact.prevent="handleSend"
    >
      <template #append-inner>
        <v-btn
          v-if="isStreaming"
          icon="mdi-stop"
          size="small"
          color="error"
          variant="tonal"
          @click="$emit('stop')"
        />
        <v-btn
          v-else
          icon="mdi-send"
          size="small"
          color="primary"
          variant="tonal"
          :disabled="!inputText.trim() || disabled"
          @click="handleSend"
        />
      </template>
    </v-textarea>
  </div>
</template>

<script lang="ts" setup>
import { ref, watch } from 'vue'

const props = defineProps<{
  disabled?: boolean
  isStreaming?: boolean
}>()

const emit = defineEmits<{
  send: [text: string]
  stop: []
}>()

const inputText = ref('')
const textareaRef = ref<any>(null)

function handleSend() {
  const text = inputText.value.trim()
  if (!text || props.disabled) return
  emit('send', text)
  inputText.value = ''
}

watch(
  () => props.isStreaming,
  (streaming) => {
    if (!streaming) {
      setTimeout(() => {
        textareaRef.value?.focus()
      }, 100)
    }
  },
)
</script>

<style scoped>
.chat-input {
  padding: 8px 16px 12px;
}
.chat-input__textarea :deep(.v-field) {
  border-radius: 20px !important;
}
</style>
