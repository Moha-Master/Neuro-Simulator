<template>
  <div class="tag-input">
    <div
      class="tag-input__field"
      :class="{ 'tag-input__field--focused': focused, 'tag-input__field--invalid': !!error }"
      @click="focusInput"
    >
      <v-chip
        v-for="tag in modelValue"
        :key="tag"
        class="tag-input__chip"
        size="small"
        closable
        @click:close="remove(tag)"
      >
        {{ tag }}
      </v-chip>

      <input
        ref="inputEl"
        v-model="draft"
        class="tag-input__input"
        type="text"
        :placeholder="placeholder"
        :disabled="disabled"
        @keydown.enter.prevent="commit"
        @keydown.backspace="onBackspace"
        @focus="focused = true"
        @blur="focused = false"
        @input="error = ''"
      />
    </div>

    <div class="tag-input__hint">
      <span>最多 {{ max }} 个标签，每个最多 {{ maxLength }} 个字符；回车添加。</span>
      <span v-if="error" class="tag-input__error">{{ error }}</span>
      <span v-else class="tag-input__count">{{ modelValue.length }}/{{ max }}</span>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { ref } from 'vue'

const props = withDefaults(
  defineProps<{
    max?: number
    maxLength?: number
    placeholder?: string
    disabled?: boolean
  }>(),
  {
    max: 10,
    maxLength: 25,
    placeholder: '添加标签…',
    disabled: false,
  },
)

const modelValue = defineModel<string[]>({ default: () => [] })

const draft = ref('')
const error = ref('')
const focused = ref(false)
const inputEl = ref<HTMLInputElement | null>(null)

function focusInput(): void {
  inputEl.value?.focus()
}

function commit(): void {
  const tag = draft.value.trim()
  if (!tag) return
  if (tag.length > props.maxLength) {
    error.value = `单个标签最多 ${props.maxLength} 个字符`
    return
  }
  if (modelValue.value.some((t) => t.toLowerCase() === tag.toLowerCase())) {
    draft.value = ''
    error.value = '标签已存在'
    return
  }
  if (modelValue.value.length >= props.max) {
    error.value = `最多只能添加 ${props.max} 个标签`
    return
  }
  modelValue.value = [...modelValue.value, tag]
  draft.value = ''
  error.value = ''
}

function remove(tag: string): void {
  modelValue.value = modelValue.value.filter((t) => t !== tag)
  error.value = ''
}

function onBackspace(): void {
  if (draft.value || !modelValue.value.length) return
  modelValue.value = modelValue.value.slice(0, -1)
  error.value = ''
}
</script>

<style scoped>
.tag-input {
  width: 100%;
}

.tag-input__field {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  min-height: 40px;
  padding: 4px 8px;
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 4px;
  background: rgb(var(--v-theme-surface));
  cursor: text;
  transition: border-color 0.15s ease;
}

.tag-input__field--focused {
  border-color: rgb(var(--v-theme-primary));
}

.tag-input__field--invalid {
  border-color: rgb(var(--v-theme-error));
}

.tag-input__chip {
  max-width: 100%;
}

.tag-input__input {
  flex: 1 1 120px;
  min-width: 100px;
  height: 26px;
  padding: 0 2px;
  border: 0;
  outline: 0;
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font-family: inherit;
  font-size: 14px;
}

.tag-input__input::placeholder {
  color: rgba(var(--v-theme-on-surface), 0.45);
}

.tag-input__hint {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-top: 4px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 12px;
  line-height: 16px;
}

.tag-input__error {
  color: rgb(var(--v-theme-error));
}

.tag-input__count {
  flex-shrink: 0;
}
</style>
