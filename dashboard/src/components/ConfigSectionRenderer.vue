<template>
  <div class="config-section">
    <!-- For arrays, render each object as an expandable panel -->
    <div v-if="Array.isArray(value)">
      <v-card variant="outlined" class="mb-2">
        <v-card-title class="text-subtitle-1 py-2">
          <v-icon class="mr-2">mdi-format-list-bulleted</v-icon>
          {{ formatTitle(itemKey) }}
        </v-card-title>
        <v-card-text class="py-2">
          <v-expansion-panels multiple>
            <v-expansion-panel
              v-for="(item, index) in value"
              :key="`${itemKey}-${index}`"
              class="mb-2"
            >
              <v-expansion-panel-title>
                <v-icon class="mr-2">mdi-cube</v-icon>
                {{ getItemTitle(item, index) }}
              </v-expansion-panel-title>
              <v-expansion-panel-text>
                <div v-for="(subValue, subKey) in item" :key="`${itemKey}-${index}-${String(subKey)}`" class="mb-2">
                  <v-text-field
                    :label="formatTitle(String(subKey))"
                    :model-value="subValue"
                    @update:model-value="updateArrayItemValue(index, String(subKey), $event)"
                    :disabled="String(subKey) === 'id'"
                    variant="outlined"
                    hide-details="auto"
                  ></v-text-field>
                </div>

                <!-- Action buttons for this item -->
                <div class="d-flex justify-end mt-4">
                  <v-btn
                    size="small"
                    variant="text"
                    color="primary"
                    @click="duplicateItem(index)"
                    class="mr-2"
                  >
                    <v-icon left>mdi-content-copy</v-icon>
                    Copy
                  </v-btn>
                  <v-btn
                    size="small"
                    variant="text"
                    color="error"
                    @click="deleteItem(index)"
                  >
                    <v-icon left>mdi-delete</v-icon>
                    Delete
                  </v-btn>
                </div>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>

          <!-- Add new item button -->
          <div class="mt-4 d-flex justify-end">
            <v-btn
              color="primary"
              @click="addItemToArray"
              variant="elevated"
            >
              <v-icon left>mdi-plus</v-icon>
              Add New {{ formatTitle(itemKey).replace(/s$/, '') }}
            </v-btn>
          </div>
        </v-card-text>
      </v-card>
    </div>
    <!-- For objects (not in arrays), render their key-value pairs -->
    <div v-else-if="typeof value === 'object' && value !== null">
      <v-card variant="outlined" class="mb-2">
        <v-card-title class="text-subtitle-1 py-2">
          <v-icon class="mr-2">mdi-cog</v-icon>
          {{ formatTitle(itemKey) }}
        </v-card-title>
        <v-card-text class="py-2">
          <div v-for="(subValue, subKey) in value" :key="`${itemKey}.${String(subKey)}`" class="mb-2">
            <v-text-field
              :label="formatTitle(String(subKey))"
              :model-value="subValue"
              @update:model-value="updateObjectValue(String(subKey), $event)"
              :disabled="String(subKey) === 'id'"
              variant="outlined"
              hide-details="auto"
            ></v-text-field>
          </div>
        </v-card-text>
      </v-card>
    </div>
    <!-- For primitive values, render as simple input or id selector if key ends with '_id' -->
    <div v-else class="primitive-value mb-2">
      <!-- Check if the key ends with '_id' -->
      <IdReferenceSelector
        v-if="isIdReferenceKey(itemKey)"
        :label="formatTitle(itemKey)"
        :value="value"
        :items="getIdReferenceOptions(itemKey)"
        @update:value="updateValue(itemKey, $event)"
      />
      <v-text-field
        v-else
        :label="formatTitle(itemKey)"
        :model-value="value"
        @update:model-value="updateValue(itemKey, $event)"
        variant="outlined"
        hide-details="auto"
      ></v-text-field>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import { v4 as uuidv4 } from 'uuid'
import IdReferenceSelector from './IdReferenceSelector.vue'

// Define props
interface Props {
  itemKey: string;
  value: any;
  parentKey: string;
  configMap?: Record<string, any>;
}

const props = defineProps<Props>()

// Define emits
const emit = defineEmits<{
  'update-value': [value: any]
}>()

// Format title (capitalize first letter)
const formatTitle = (title: string) => {
  return title.charAt(0).toUpperCase() + title.slice(1)
}

// Get item title from name field or default
const getItemTitle = (item: any, index: number) => {
  if (item && typeof item === 'object' && item.name) {
    return item.name
  }
  return `${formatTitle(props.itemKey)} Item ${index + 1}`
}

// Update array item value
const updateArrayItemValue = (index: number, subKey: string, newValue: any) => {
  const newArray = [...props.value]
  const newItem = { ...newArray[index] }
  newItem[subKey] = newValue
  newArray[index] = newItem
  emit('update-value', newArray)
}

// Update object value
const updateObjectValue = (subKey: string, newValue: any) => {
  const newObject = { ...props.value }
  newObject[subKey] = newValue
  emit('update-value', newObject)
}

// Check if the key is an id reference (ends with '_id')
const isIdReferenceKey = (key: string) => {
  return key.endsWith('_id') && key !== '_id'
}

// Get options for id reference selector
const getIdReferenceOptions = (key: string) => {
  if (!props.configMap) {
    console.log(`Config map is null for key: ${key}`)
    return []
  }

  // Determine which type of items to look for based on the key
  // For example, if key is 'llm_service_id', look for 'llm_services' in the config
  const referenceType = key.slice(0, -3) // Remove '_id' suffix
  const pluralReferenceType = referenceType + 's' // Make it plural

  console.log(`Looking for reference type: ${referenceType}, plural: ${pluralReferenceType}`)
  console.log(`Available config keys:`, Object.keys(props.configMap))

  // Look for matching array in all sections of the config map
  let items: any[] = []

  // Iterate through all config sections to find the matching array
  for (const sectionName in props.configMap) {
    const section = props.configMap[sectionName]
    if (section && typeof section === 'object' && section[pluralReferenceType]) {
      const sectionItems = section[pluralReferenceType]
      if (Array.isArray(sectionItems)) {
        items = items.concat(sectionItems)
        console.log(`Found ${sectionItems.length} items in section ${sectionName}`)
      }
    }
  }

  console.log(`Found items for ${pluralReferenceType}:`, items)

  if (Array.isArray(items)) {
    // Filter items that have both id and name
    const filteredItems = items.filter(item => item && item.id && item.name)
    console.log(`Filtered items:`, filteredItems)
    return filteredItems
  }

  console.log(`Items is not an array or doesn't exist`)
  return []
}

// Handle primitive value updates
const updateValue = (key: string, value: any) => {
  emit('update-value', value)
}

// Add new item to array
const addItemToArray = () => {
  // Create a new item with default values
  const newItem: any = {}

  // If the array is not empty, use the first item as a template
  if (props.value && props.value.length > 0) {
    const firstItem = props.value[0]
    // Copy all keys from the first item with empty/default values
    for (const key in firstItem) {
      if (key === 'id') {
        // Generate a UUID if the key is 'id'
        newItem[key] = uuidv4()
      } else {
        // Set other fields to empty/default values based on their type
        const originalValue = firstItem[key]
        if (typeof originalValue === 'string') {
          newItem[key] = ''
        } else if (typeof originalValue === 'number') {
          newItem[key] = 0
        } else if (typeof originalValue === 'boolean') {
          newItem[key] = false
        } else if (Array.isArray(originalValue)) {
          newItem[key] = []
        } else if (typeof originalValue === 'object' && originalValue !== null) {
          newItem[key] = {}
        } else {
          newItem[key] = null
        }
      }
    }
  } else {
    // If the array is empty, create a minimal item with an ID if needed
    newItem.id = uuidv4()
  }

  // Add the new item to the array
  const newArray = Array.from(props.value)
  newArray.push(newItem)
  emit('update-value', newArray)
}

// Duplicate an item in the array
const duplicateItem = (index: number) => {
  if (index < 0 || index >= props.value.length) return

  const itemToDuplicate = props.value[index]
  const duplicatedItem: any = { ...itemToDuplicate }

  // Generate a new UUID for the duplicated item
  if (duplicatedItem.id) {
    duplicatedItem.id = uuidv4()
  }

  // Add the duplicated item to the array
  const newArray = Array.from(props.value)
  newArray.push(duplicatedItem)
  emit('update-value', newArray)
}

// Delete an item from the array
const deleteItem = (index: number) => {
  if (index < 0 || index >= props.value.length) return

  // Create a new array without the item at the specified index
  const newArray = props.value.filter((_: any, i: number) => i !== index)
  emit('update-value', newArray)
}
</script>

<style scoped>
.font-mono {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
}
</style>