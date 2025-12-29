<template>
  <v-select
    :label="label"
    :items="selectItems"
    :model-value="selectedValue"
    @update:model-value="onUpdateValue"
    item-title="name"
    item-value="id"
    variant="outlined"
    hide-details="auto"
    :disabled="selectItems.length === 0"
    :placeholder="selectItems.length === 0 ? 'No options available' : undefined"
  >
    <template #item="{ props, item }">
      <v-list-item
        v-bind="props"
        :title="`${item.raw.name}`"
        :subtitle="item.raw.id"
      ></v-list-item>
    </template>
    <template #selection="{ item }">
      <span>{{ item.raw?.name || item.raw }}</span>
    </template>
  </v-select>
</template>

<script lang="ts" setup>
import { computed } from 'vue'

interface Item {
  id: string;
  name: string;
  [key: string]: any;
}

interface SelectItem {
  id: string;
  name: string;
}

interface Props {
  label: string;
  value: string;
  items: Item[];
}

const props = defineProps<Props>()

const emit = defineEmits<{
  'update:value': [value: string]
}>()

// Prepare items for the select component
const selectItems = computed(() => {
  return props.items.map(item => ({
    id: item.id,
    name: item.name || item.id
  }))
})

// Current selected value
const selectedValue = computed(() => {
  return props.value
})

// Handle value update
const onUpdateValue = (newValue: string) => {
  emit('update:value', newValue)
}
</script>