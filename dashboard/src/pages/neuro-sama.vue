<template>
  <v-container>
    <v-card>
      <v-card-title>Neuro Sama Management</v-card-title>
      <v-card-subtitle>View and manage Neuro Sama context</v-card-subtitle>
      <v-card-text>
        <!-- Connection status indicator -->
        <v-alert
          :type="connectionStore.isNeuroSamaConnected ? 'success' : 'error'"
          variant="tonal"
          class="mb-4"
        >
          <div class="d-flex align-center">
            <span>Neuro Sama Connection: {{ connectionStore.isNeuroSamaConnected ? 'Connected' : 'Disconnected' }}</span>
          </div>
        </v-alert>

        <!-- Tabs for navigation -->
        <v-tabs v-model="currentTab" show-arrows class="mb-4">
          <v-tab value="context">
            Context
          </v-tab>
          <v-tab value="memory">
            Memory
          </v-tab>
          <v-tab value="chat">
            Chat
          </v-tab>
        </v-tabs>

        <v-window v-model="currentTab" class="pa-2">
          <v-window-item value="context">
            <v-card variant="outlined" class="mb-4">
              <v-card-title class="text-subtitle-1 py-2">
                <v-icon class="mr-2">mdi-text-box</v-icon>
                System Prompt
              </v-card-title>
              <v-card-text class="py-2">
                <v-expansion-panels multiple>
                  <v-expansion-panel class="mb-2">
                    <v-expansion-panel-title>
                      <v-icon class="mr-2">mdi-text-subject</v-icon>
                      System Prompt Content
                    </v-expansion-panel-title>
                    <v-expansion-panel-text>
                      <pre class="font-mono">{{ systemPrompt }}</pre>
                    </v-expansion-panel-text>
                  </v-expansion-panel>
                </v-expansion-panels>
              </v-card-text>
            </v-card>

            <v-card variant="outlined" class="mb-4">
              <v-card-title class="text-subtitle-1 py-2">
                <v-icon class="mr-2">mdi-chat</v-icon>
                Current Context
              </v-card-title>
              <v-card-text class="py-2">
                <v-expansion-panels multiple>
                  <v-expansion-panel class="mb-2">
                    <v-expansion-panel-title>
                      <v-icon class="mr-2">mdi-message-text</v-icon>
                      Current Context Content
                    </v-expansion-panel-title>
                    <v-expansion-panel-text>
                      <pre class="font-mono">{{ currentContext }}</pre>
                    </v-expansion-panel-text>
                  </v-expansion-panel>
                </v-expansion-panels>
              </v-card-text>
            </v-card>

            <div class="d-flex justify-end mt-4">
              <v-btn
                color="primary"
                @click="refreshContext"
                :disabled="isLoading"
              >
                <v-icon left>mdi-refresh</v-icon>
                Refresh
              </v-btn>
            </div>
          </v-window-item>

          <v-window-item value="memory">
            <v-card variant="outlined" class="mb-4">
              <v-card-title class="text-subtitle-1 py-2">
                <v-icon class="mr-2">mdi-cog</v-icon>
                Init Memory
              </v-card-title>
              <v-card-text class="py-2">
                <v-expansion-panels multiple>
                  <v-expansion-panel
                    v-for="(value, key) in initMemory"
                    :key="key"
                    class="mb-2"
                  >
                    <v-expansion-panel-title>
                      <v-icon class="mr-2">mdi-label</v-icon>
                      {{ formatTitle(String(key)) }}
                    </v-expansion-panel-title>
                    <v-expansion-panel-text>
                      <div v-if="typeof value === 'object' && value !== null">
                        <div v-for="(subValue, subKey) in value" :key="subKey" class="d-flex align-center mb-2">
                          <v-text-field
                            :label="formatTitle(String(subKey))"
                            :model-value="subValue"
                            @update:model-value="updateInitMemoryObjectValue(key, String(subKey), $event)"
                            variant="outlined"
                            hide-details="auto"
                            :readonly="!canEditMemory"
                            class="flex-grow-1"
                          ></v-text-field>
                          <v-btn
                            v-if="canEditMemory && Object.keys(value).length > 1"
                            icon="mdi-delete"
                            color="error"
                            variant="text"
                            @click="deleteInitMemoryObjectProperty(key, String(subKey))"
                            class="ml-2"
                          ></v-btn>
                        </div>
                        <v-btn
                          v-if="canEditMemory"
                          color="primary"
                          variant="outlined"
                          @click="addInitMemoryObjectProperty(key)"
                          class="mt-2"
                        >
                          <v-icon left>mdi-plus</v-icon>
                          Add Property
                        </v-btn>
                      </div>

                      <!-- Action buttons for this item -->
                      <div class="d-flex justify-end mt-4">
                        <v-btn
                          v-if="canEditMemory"
                          size="small"
                          variant="text"
                          color="error"
                          @click="deleteInitMemoryItem(key)"
                        >
                          <v-icon left>mdi-delete</v-icon>
                          Delete Item
                        </v-btn>
                      </div>
                    </v-expansion-panel-text>
                  </v-expansion-panel>
                </v-expansion-panels>

                <!-- Add new item button -->
                <div class="mt-4 d-flex justify-end">
                  <v-btn
                    v-if="canEditMemory"
                    color="primary"
                    @click="addInitMemoryItem"
                    variant="elevated"
                  >
                    <v-icon left>mdi-plus</v-icon>
                    Add New Memory Item
                  </v-btn>
                </div>
              </v-card-text>
            </v-card>

            <v-card variant="outlined" class="mb-4">
              <v-card-title class="text-subtitle-1 py-2">
                <v-icon class="mr-2">mdi-cube</v-icon>
                Core Memory
              </v-card-title>
              <v-card-text class="py-2">
                <v-expansion-panels multiple>
                  <v-expansion-panel
                    v-for="(block, blockId) in coreMemoryBlocks"
                    :key="blockId"
                    class="mb-2"
                  >
                    <v-expansion-panel-title>
                      <v-icon class="mr-2">mdi-cube</v-icon>
                      {{ block.title || blockId }}
                    </v-expansion-panel-title>
                    <v-expansion-panel-text>
                      <v-text-field
                        label="ID"
                        :model-value="block.id"
                        @update:model-value="updateCoreMemoryBlockValue(String(blockId), 'id', $event)"
                        variant="outlined"
                        hide-details="auto"
                        :readonly="!canEditMemory"
                      ></v-text-field>
                      <v-text-field
                        label="Title"
                        :model-value="block.title"
                        @update:model-value="updateCoreMemoryBlockValue(String(blockId), 'title', $event)"
                        variant="outlined"
                        hide-details="auto"
                        class="mt-2"
                        :readonly="!canEditMemory"
                      ></v-text-field>
                      <v-textarea
                        label="Description"
                        :model-value="block.description"
                        @update:model-value="updateCoreMemoryBlockValue(String(blockId), 'description', $event)"
                        rows="3"
                        variant="outlined"
                        hide-details="auto"
                        class="mt-2"
                        :readonly="!canEditMemory"
                      ></v-textarea>
                      <v-card variant="outlined" class="mt-2">
                        <v-card-title class="text-subtitle-2 py-1">
                          <v-icon class="mr-2">mdi-format-list-bulleted</v-icon>
                          Content
                        </v-card-title>
                        <v-card-text class="py-2">
                          <div v-for="(contentItem, index) in block.content" :key="index" class="d-flex align-center mb-2">
                            <v-textarea
                              :model-value="contentItem"
                              @update:model-value="updateCoreMemoryContentValue(String(blockId), Number(index), $event)"
                              rows="2"
                              variant="outlined"
                              hide-details="auto"
                              :readonly="!canEditMemory"
                            ></v-textarea>
                            <v-btn
                              v-if="canEditMemory"
                              icon="mdi-delete"
                              color="error"
                              variant="text"
                              @click="removeCoreMemoryContentItem(String(blockId), Number(index))"
                              class="ml-2"
                            ></v-btn>
                          </div>
                          <v-btn
                            v-if="canEditMemory"
                            color="primary"
                            variant="outlined"
                            @click="addCoreMemoryContentItem(String(blockId))"
                            class="mt-2"
                          >
                            <v-icon left>mdi-plus</v-icon>
                            Add Content Item
                          </v-btn>
                        </v-card-text>
                      </v-card>

                      <!-- Action buttons for this block -->
                      <div class="d-flex justify-end mt-4">
                        <v-btn
                          v-if="canEditMemory"
                          size="small"
                          variant="text"
                          color="error"
                          @click="deleteCoreMemoryBlock(String(blockId))"
                        >
                          <v-icon left>mdi-delete</v-icon>
                          Delete Block
                        </v-btn>
                      </div>
                    </v-expansion-panel-text>
                  </v-expansion-panel>
                </v-expansion-panels>

                <!-- Add new block button -->
                <div class="mt-4 d-flex justify-end">
                  <v-btn
                    v-if="canEditMemory"
                    color="primary"
                    @click="addCoreMemoryBlock"
                    variant="elevated"
                  >
                    <v-icon left>mdi-plus</v-icon>
                    Add New Memory Block
                  </v-btn>
                </div>
              </v-card-text>
            </v-card>

            <v-card variant="outlined" class="mt-4">
              <v-card-title class="text-subtitle-1 py-2">
                <v-icon class="mr-2">mdi-notebook</v-icon>
                Temp Memory
              </v-card-title>
              <v-card-text class="py-2">
                <v-expansion-panels multiple>
                  <v-expansion-panel
                    v-for="(item, index) in tempMemoryItems"
                    :key="item.id || index"
                    class="mb-2"
                  >
                    <v-expansion-panel-title>
                      <v-icon class="mr-2">mdi-note</v-icon>
                      {{ truncateContent(item.content || 'No content') }}
                    </v-expansion-panel-title>
                    <v-expansion-panel-text>
                      <v-text-field
                        label="ID"
                        :model-value="item.id"
                        @update:model-value="updateTempMemoryValue(Number(index), 'id', $event)"
                        variant="outlined"
                        hide-details="auto"
                        :readonly="!canEditMemory"
                      ></v-text-field>
                      <v-text-field
                        label="Role"
                        :model-value="item.role"
                        @update:model-value="updateTempMemoryValue(Number(index), 'role', $event)"
                        variant="outlined"
                        hide-details="auto"
                        class="mt-2"
                        :readonly="!canEditMemory"
                      ></v-text-field>
                      <v-text-field
                        label="Timestamp"
                        :model-value="item.timestamp"
                        @update:model-value="updateTempMemoryValue(Number(index), 'timestamp', $event)"
                        variant="outlined"
                        hide-details="auto"
                        class="mt-2"
                        :readonly="!canEditMemory"
                      ></v-text-field>
                      <v-textarea
                        label="Content"
                        :model-value="item.content"
                        @update:model-value="updateTempMemoryValue(Number(index), 'content', $event)"
                        rows="3"
                        variant="outlined"
                        hide-details="auto"
                        class="mt-2"
                        :readonly="!canEditMemory"
                      ></v-textarea>

                      <!-- Action buttons for this item -->
                      <div class="d-flex justify-end mt-4">
                        <v-btn
                          v-if="canEditMemory"
                          size="small"
                          variant="text"
                          color="error"
                          @click="deleteTempMemoryItem(Number(index))"
                        >
                          <v-icon left>mdi-delete</v-icon>
                          Delete Item
                        </v-btn>
                      </div>
                    </v-expansion-panel-text>
                  </v-expansion-panel>
                </v-expansion-panels>

                <!-- Add new item button -->
                <div class="mt-4 d-flex justify-end">
                  <v-btn
                    v-if="canEditMemory"
                    color="primary"
                    @click="addTempMemoryItem"
                    variant="elevated"
                  >
                    <v-icon left>mdi-plus</v-icon>
                    Add New Memory Item
                  </v-btn>
                </div>
              </v-card-text>
            </v-card>

            <div class="d-flex justify-end mt-4">
              <v-btn
                color="primary"
                @click="refreshMemory"
                :disabled="isLoading"
                class="mr-2"
              >
                <v-icon left>mdi-refresh</v-icon>
                Refresh
              </v-btn>
              <v-btn
                color="success"
                @click="saveMemory"
                :disabled="isLoading || !canEditMemory"
              >
                <v-icon left>mdi-content-save</v-icon>
                Save
              </v-btn>
            </div>
          </v-window-item>

          <v-window-item value="chat">
            <v-card variant="outlined" class="mb-4">
              <v-card-title class="text-subtitle-1 py-2">
                <v-icon class="mr-2">mdi-message-text</v-icon>
                Private Chat
              </v-card-title>
              <v-card-text class="py-4">
                <v-row>
                  <v-col cols="12">
                    <v-textarea
                      label="Context"
                      v-model="chatContext"
                      variant="outlined"
                      hide-details="auto"
                      :readonly="!canEditMemory"
                      rows="3"
                      class="mb-4"
                    ></v-textarea>
                  </v-col>
                </v-row>

                <v-row>
                  <v-col cols="12" md="4">
                    <v-text-field
                      label="Username"
                      v-model="chatUsername"
                      variant="outlined"
                      hide-details="auto"
                      :readonly="!canEditMemory"
                    ></v-text-field>
                  </v-col>
                  <v-col cols="12" md="4">
                    <v-text-field
                      label="User Message"
                      v-model="chatUserMessage"
                      variant="outlined"
                      hide-details="auto"
                      :readonly="!canEditMemory"
                    ></v-text-field>
                  </v-col>
                  <v-col cols="12" md="4">
                    <v-switch
                      v-model="enableAudio"
                      label="Enable Audio"
                      color="primary"
                      hide-details
                      :disabled="!canEditMemory"
                    ></v-switch>
                  </v-col>
                </v-row>

                <v-row class="mt-4">
                  <v-col cols="12">
                    <v-btn
                      color="primary"
                      @click="sendChatMessage"
                      :disabled="isLoading || !canEditMemory"
                      class="mr-4"
                    >
                      <v-icon left>mdi-send</v-icon>
                      Send
                    </v-btn>
                  </v-col>
                </v-row>

                <v-row class="mt-4">
                  <v-col cols="12">
                    <v-card variant="tonal" class="pa-4">
                      <v-card-title class="text-subtitle-1 py-2">
                        <v-icon class="mr-2">mdi-account-voice</v-icon>
                        Neuro's Response
                      </v-card-title>
                      <v-card-text class="py-2">
                        <pre class="font-mono">{{ neuroResponse }}</pre>

                        <!-- Audio status information integrated into the response card -->
                        <div v-if="neuroAudioQueue.length > 0" class="mt-2 caption">
                          <span v-if="currentPlayingAudio">🔊 Playing audio {{ getCurrentPlayingIndex() + 1 }} of {{ neuroAudioQueue.length }}</span>
                          <span v-else>🎵 {{ neuroAudioQueue.length }} audio{{ neuroAudioQueue.length > 1 ? 's' : '' }} queued (auto-playing)</span>
                        </div>

                        <!-- Hidden audio element for background playback -->
                        <audio
                          v-if="currentPlayingAudio && getCurrentPlayingAudio()"
                          :id="`audio_${currentPlayingAudio}`"
                          :src="`data:audio/wav;base64,${getCurrentPlayingAudio()?.audio}`"
                          autoplay
                          @ended="finishCurrentAudio"
                          @error="finishCurrentAudio"
                          style="display: none;"
                        ></audio>
                      </v-card-text>
                    </v-card>
                  </v-col>
                </v-row>
              </v-card-text>
            </v-card>
          </v-window-item>
        </v-window>
      </v-card-text>
    </v-card>
  </v-container>
</template>

<script lang="ts" setup>
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { useConnectionStore } from '@/stores/connection'

const connectionStore = useConnectionStore()
const currentTab = ref('context')
const systemPrompt = ref('')
const currentContext = ref('')
const initMemory = ref<Record<string, any>>({})
const coreMemory = ref('')
const tempMemory = ref('')
const isLoading = ref(false)
const canEditMemory = computed(() => connectionStore.isNeuroSamaConnected)

// Chat tab related data
const chatContext = ref('PRIVATE DM CONTEXT: User is having a private conversation with Neuro.')
const chatUsername = ref('vedal987')
const chatUserMessage = ref('')
const neuroResponse = ref('')
// Audio queue system for sequential playback
const neuroAudioQueue = ref<Array<{id: string, audio: string, duration: number}>>([])
const currentPlayingAudio = ref<string | null>(null) // Track currently playing audio ID
const enableAudio = ref(false) // Audio toggle switch

// Computed property to extract core memory blocks
const coreMemoryBlocks = computed(() => {
  try {
    const parsed = JSON.parse(coreMemory.value)
    return parsed.blocks || {}
  } catch {
    return {}
  }
})

// Computed property to extract temp memory items
const tempMemoryItems = computed(() => {
  try {
    return JSON.parse(tempMemory.value) || []
  } catch {
    return []
  }
})

// Format title (capitalize first letter)
const formatTitle = (title: string) => {
  return title.charAt(0).toUpperCase() + title.slice(1)
}

// Format timestamp for display
const formatTimestamp = (timestamp: string) => {
  if (!timestamp) return 'No timestamp'
  try {
    const date = new Date(timestamp)
    return date.toLocaleString()
  } catch {
    return timestamp // Return original if parsing fails
  }
}

// Truncate content for display in expansion panel title
const truncateContent = (content: string, maxLength: number = 50) => {
  if (!content) return 'No content'
  if (content.length <= maxLength) return content
  return content.substring(0, maxLength) + '...'
}

// Update a specific value in a temp memory item
const updateTempMemoryValue = (index: number, field: string, value: any) => {
  try {
    const parsed = JSON.parse(tempMemory.value)
    const items = Array.isArray(parsed) ? parsed : []
    const item = items[index] || {}

    // Update the specific field
    const updatedItem = { ...item, [field]: value }

    // Update the items array
    const updatedItems = [...items]
    updatedItems[index] = updatedItem

    // Update the tempMemory value
    tempMemory.value = JSON.stringify(updatedItems, null, 2)
  } catch (error) {
    console.error('Error updating temp memory value:', error)
  }
}

// Add a new temp memory item
const addTempMemoryItem = () => {
  try {
    const parsed = JSON.parse(tempMemory.value)
    const items = Array.isArray(parsed) ? parsed : []

    // Generate a new item with default values
    const newItem = {
      id: Math.random().toString(36).substring(2, 8), // Random 6-char ID
      content: 'New temporary memory content',
      role: 'assistant',
      timestamp: new Date().toISOString()
    }

    // Add the new item to the array
    const updatedItems = [...items, newItem]

    // Update the tempMemory value
    tempMemory.value = JSON.stringify(updatedItems, null, 2)
  } catch (error) {
    console.error('Error adding temp memory item:', error)
  }
}

// Delete a temp memory item
const deleteTempMemoryItem = (index: number) => {
  try {
    const parsed = JSON.parse(tempMemory.value)
    const items = Array.isArray(parsed) ? parsed : []

    // Remove the item at the specified index
    const updatedItems = items.filter((_: any, i: number) => i !== index)

    // Update the tempMemory value
    tempMemory.value = JSON.stringify(updatedItems, null, 2)
  } catch (error) {
    console.error('Error deleting temp memory item:', error)
  }
}

// Update a specific value in initMemory
const updateInitMemoryValue = (key: string, value: any) => {
  // Always store as an object with a 'value' property for consistency
  const processedValue = { value: value };

  initMemory.value = { ...initMemory.value, [key]: processedValue }
}

// Delete an item from initMemory
const deleteInitMemoryItem = (key: string) => {
  const { [key]: _, ...rest } = initMemory.value
  initMemory.value = rest
}

// Add a new item to initMemory
const addInitMemoryItem = () => {
  // Generate a new key
  let newKey = 'new_item'
  let counter = 1
  while (initMemory.value[newKey]) {
    newKey = `new_item_${counter}`
    counter++
  }

  // Add the new item with a default value
  initMemory.value = { ...initMemory.value, [newKey]: '' }
}

// Delete a property from an object in initMemory
const deleteInitMemoryObjectProperty = (key: string, subKey: string) => {
  const currentObj = initMemory.value[key]
  if (typeof currentObj === 'object' && currentObj !== null) {
    const { [subKey]: _, ...rest } = currentObj
    initMemory.value = {
      ...initMemory.value,
      [key]: rest
    }
  }
}

// Add a property to an object in initMemory
const addInitMemoryObjectProperty = (key: string) => {
  const currentObj = initMemory.value[key]
  if (typeof currentObj === 'object' && currentObj !== null) {
    // Generate a new property name
    let newSubKey = 'new_property'
    let counter = 1
    while (currentObj[newSubKey]) {
      newSubKey = `new_property_${counter}`
      counter++
    }

    initMemory.value = {
      ...initMemory.value,
      [key]: { ...currentObj, [newSubKey]: '' }
    }
  }
}

// Update a specific value in an object within initMemory
const updateInitMemoryObjectValue = (key: string, subKey: string, value: any) => {
  const currentObj = initMemory.value[key]
  if (typeof currentObj === 'object' && currentObj !== null) {
    initMemory.value = {
      ...initMemory.value,
      [key]: { ...currentObj, [subKey]: value }
    }
  }
}

// Update a specific value in a core memory block
const updateCoreMemoryBlockValue = (blockId: string, field: string, value: any) => {
  try {
    const parsed = JSON.parse(coreMemory.value)
    const blocks = parsed.blocks || {}
    const block = blocks[blockId] || {}

    // Update the specific field
    const updatedBlock = { ...block, [field]: value }

    // Update the blocks object
    const updatedBlocks = { ...blocks, [blockId]: updatedBlock }

    // Update the coreMemory value
    coreMemory.value = JSON.stringify({ ...parsed, blocks: updatedBlocks }, null, 2)
  } catch (error) {
    console.error('Error updating core memory block value:', error)
  }
}

// Update a content item in a core memory block
const updateCoreMemoryContentValue = (blockId: string, index: number, value: string) => {
  try {
    const parsed = JSON.parse(coreMemory.value)
    const blocks = parsed.blocks || {}
    const block = blocks[blockId] || {}
    const content = block.content || []

    // Update the content item at the specified index
    const updatedContent = [...content]
    updatedContent[index] = value

    // Update the block with the new content
    const updatedBlock = { ...block, content: updatedContent }

    // Update the blocks object
    const updatedBlocks = { ...blocks, [blockId]: updatedBlock }

    // Update the coreMemory value
    coreMemory.value = JSON.stringify({ ...parsed, blocks: updatedBlocks }, null, 2)
  } catch (error) {
    console.error('Error updating core memory content value:', error)
  }
}

// Add a new content item to a core memory block
const addCoreMemoryContentItem = (blockId: string) => {
  try {
    const parsed = JSON.parse(coreMemory.value)
    const blocks = parsed.blocks || {}
    const block = blocks[blockId] || {}
    const content = block.content || []

    // Add a new empty content item
    const updatedContent = [...content, '']

    // Update the block with the new content
    const updatedBlock = { ...block, content: updatedContent }

    // Update the blocks object
    const updatedBlocks = { ...blocks, [blockId]: updatedBlock }

    // Update the coreMemory value
    coreMemory.value = JSON.stringify({ ...parsed, blocks: updatedBlocks }, null, 2)
  } catch (error) {
    console.error('Error adding core memory content item:', error)
  }
}

// Remove a content item from a core memory block
const removeCoreMemoryContentItem = (blockId: string, index: number) => {
  try {
    const parsed = JSON.parse(coreMemory.value)
    const blocks = parsed.blocks || {}
    const block = blocks[blockId] || {}
    const content = block.content || []

    // Remove the content item at the specified index
    const updatedContent = content.filter((_: any, i: number) => i !== index)

    // Update the block with the new content
    const updatedBlock = { ...block, content: updatedContent }

    // Update the blocks object
    const updatedBlocks = { ...blocks, [blockId]: updatedBlock }

    // Update the coreMemory value
    coreMemory.value = JSON.stringify({ ...parsed, blocks: updatedBlocks }, null, 2)
  } catch (error) {
    console.error('Error removing core memory content item:', error)
  }
}

// Add a new core memory block
const addCoreMemoryBlock = () => {
  try {
    const parsed = JSON.parse(coreMemory.value)
    const blocks = parsed.blocks || {}

    // Generate a new block ID
    let newBlockId = 'new_block'
    let counter = 1
    while (blocks[newBlockId]) {
      newBlockId = `new_block_${counter}`
      counter++
    }

    // Create a new block with default values
    const newBlock = {
      id: newBlockId,
      title: `New Block ${counter}`,
      description: 'Description for new memory block',
      content: ['New content item']
    }

    // Update the blocks object
    const updatedBlocks = { ...blocks, [newBlockId]: newBlock }

    // Update the coreMemory value
    coreMemory.value = JSON.stringify({ ...parsed, blocks: updatedBlocks }, null, 2)
  } catch (error) {
    console.error('Error adding core memory block:', error)
  }
}

// Delete a core memory block
const deleteCoreMemoryBlock = (blockId: string) => {
  try {
    const parsed = JSON.parse(coreMemory.value)
    const blocks = parsed.blocks || {}

    // Remove the block with the specified ID
    const updatedBlocks = { ...blocks }
    delete updatedBlocks[blockId]

    // Update the coreMemory value
    coreMemory.value = JSON.stringify({ ...parsed, blocks: updatedBlocks }, null, 2)
  } catch (error) {
    console.error('Error deleting core memory block:', error)
  }
}

// Load initial data if connected
const loadData = async () => {
  if (connectionStore.isNeuroSamaConnected) {
    try {
      isLoading.value = true
      if (currentTab.value === 'context') {
        // Request initial context data
        await connectionStore.sendNeuroSamaWsMessage('get_context', {})
      } else if (currentTab.value === 'memory') {
        // Request initial memory data
        await connectionStore.sendNeuroSamaWsMessage('get_memory', {})
      }
    } catch (error) {
      console.error('Failed to load initial data:', error)
    } finally {
      isLoading.value = false
    }
  }
}

// Refresh context data
const refreshContext = async () => {
  if (connectionStore.isNeuroSamaConnected) {
    try {
      isLoading.value = true
      await connectionStore.sendNeuroSamaWsMessage('get_context', {})
    } catch (error) {
      console.error('Failed to refresh context:', error)
    } finally {
      isLoading.value = false
    }
  }
}

// Refresh memory data
const refreshMemory = async () => {
  if (connectionStore.isNeuroSamaConnected) {
    try {
      isLoading.value = true
      await connectionStore.sendNeuroSamaWsMessage('get_memory', {})
    } catch (error) {
      console.error('Failed to refresh memory:', error)
    } finally {
      isLoading.value = false
    }
  }
}

// 监听标签页变化，切换到标签页时自动获取数据
watch(currentTab, async (newTab) => {
  if (connectionStore.isNeuroSamaConnected) {
    try {
      isLoading.value = true
      if (newTab === 'memory') {
        await connectionStore.sendNeuroSamaWsMessage('get_memory', {})
      } else if (newTab === 'context') {
        await connectionStore.sendNeuroSamaWsMessage('get_context', {})
      }
    } catch (error) {
      console.error(`Failed to load ${newTab} data:`, error)
    } finally {
      isLoading.value = false
    }
  }
})

// Save memory data
const saveMemory = async () => {
  if (connectionStore.isNeuroSamaConnected) {
    try {
      isLoading.value = true
      // Parse core and temp memory from JSON strings
      let coreMemoryObj, tempMemoryObj
      try {
        const parsedCoreMemory = JSON.parse(coreMemory.value)
        // Extract the blocks from the core memory object
        coreMemoryObj = parsedCoreMemory.blocks || {}
        tempMemoryObj = JSON.parse(tempMemory.value)
      } catch (parseError) {
        throw new Error(`Invalid JSON format in core or temp memory: ${parseError}`)
      }

      const memoryData = {
        init_memory: initMemory.value,
        core_memory: coreMemoryObj,
        temp_memory: tempMemoryObj
      }

      await connectionStore.sendNeuroSamaWsMessage('update_memory', { memory: memoryData })
    } catch (error) {
      console.error('Failed to save memory:', error)
      alert('Failed to save memory: ' + (error instanceof Error ? error.message : 'Invalid JSON format'))
    } finally {
      isLoading.value = false
    }
  }
}

// Handle incoming messages from Neuro Sama WebSocket (MessageEvent)
const handleMessageEvent = (event: MessageEvent) => {
  try {
    const data = JSON.parse(event.data)

    if (data.type === 'context_update') {
      systemPrompt.value = data.payload.system_prompt || ''
      currentContext.value = data.payload.current_context || ''
      // Reset loading state after receiving data
      isLoading.value = false
    } else if (data.type === 'memory_update') {
      // Set initMemory and coreMemory as objects, tempMemory as JSON string
      initMemory.value = data.payload.init_memory || {}
      coreMemory.value = JSON.stringify({ blocks: data.payload.core_memory || {} }, null, 2)
      tempMemory.value = JSON.stringify(data.payload.temp_memory || [], null, 2)
      // Reset loading state after receiving data
      isLoading.value = false
    } else if (data.type === 'speak') {
      // Handle speak responses for context/memory tabs
      const text = data.payload.text || ''
      const audioBase64 = data.payload.audio_base64 || ''

      // For context/memory tabs, we don't accumulate responses
      if (audioBase64) {
        // Could handle audio for context/memory here if needed
      }

      // Don't reset loading state here as multiple responses may come
    } else if (data.type === 'completion') {
      // Handle completion messages - this signals the end of a response sequence
      // Reset loading state after receiving completion
      isLoading.value = false
    } else if (data.type === 'info' || data.type === 'error') {
      // Handle info or error messages
      // Reset loading state after receiving response
      isLoading.value = false
    }
  } catch (error) {
    console.error('Error parsing Neuro Sama message:', error)
    // Also reset loading state in case of error
    isLoading.value = false
  }
}

// Handle incoming messages from Neuro Sama chat (already parsed data)
const handleChatMessage = (data: any) => {
  if (data.type === 'speak') {
    // Handle speak responses for chat tab - accumulate responses
    const text = data.payload.text || ''
    const audioBase64 = data.payload.audio_base64 || ''
    const duration = data.payload.duration || 0

    // Append to existing response instead of replacing
    neuroResponse.value = neuroResponse.value ? `${neuroResponse.value}\n${text}` : text

    // Add audio to queue if available and audio is enabled
    if (audioBase64 && enableAudio.value) {
      const audioItem = {
        id: `audio_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`, // Unique ID
        audio: audioBase64,
        duration: duration
      }
      neuroAudioQueue.value = [...neuroAudioQueue.value, audioItem]

      // If no audio is currently playing, start playing
      if (!currentPlayingAudio.value) {
        playNextAudio()
      }
    }

    // Don't reset loading state here as multiple responses may come
  } else if (data.type === 'completion') {
    // Handle completion messages - this signals the end of a response sequence
    // Reset loading state after receiving completion
    isLoading.value = false
  } else if (data.type === 'info' || data.type === 'error') {
    // Handle info or error messages
    // Reset loading state after receiving response
    isLoading.value = false
  }
}

// Function to play next audio in the queue
const playNextAudio = () => {
  if (neuroAudioQueue.value.length > 0) {
    // Get the first audio in the queue
    const nextAudio = neuroAudioQueue.value[0]
    if (nextAudio) {
      currentPlayingAudio.value = nextAudio.id
      // The audio will auto-play due to the autoplay attribute in the template
    }
  } else {
    // No more audio in queue
    currentPlayingAudio.value = null
  }
}

// Function to finish current audio and move to next
const finishCurrentAudio = () => {
  // Remove the first item from the queue
  if (neuroAudioQueue.value.length > 0) {
    neuroAudioQueue.value.shift()
  }

  // Reset current playing audio
  currentPlayingAudio.value = null

  // Play next audio if available
  setTimeout(() => {
    if (neuroAudioQueue.value.length > 0) {
      playNextAudio()
    }
  }, 100) // Small delay before playing next
}

// Helper function to get the currently playing audio object
const getCurrentPlayingAudio = () => {
  if (!currentPlayingAudio.value) return null;
  return neuroAudioQueue.value.find(item => item.id === currentPlayingAudio.value) || null;
}

// Helper function to get the index of the currently playing audio
const getCurrentPlayingIndex = () => {
  if (!currentPlayingAudio.value) return -1;
  return neuroAudioQueue.value.findIndex(item => item.id === currentPlayingAudio.value);
}

// Send chat message to Neuro Sama
const sendChatMessage = async () => {
  if (!connectionStore.isNeuroSamaConnected) {
    alert('Not connected to Neuro Sama')
    return
  }

  try {
    isLoading.value = true

    // Clear previous response when starting a new chat
    neuroResponse.value = ''
    neuroAudioQueue.value = [] // Clear audio queue
    currentPlayingAudio.value = null // Reset playing state

    // Combine context, username and user message
    const fullContent = `${chatContext.value}\n${chatUsername.value}: ${chatUserMessage.value}`

    // Send message to Neuro Sama with module and audio settings
    const message = {
      content: fullContent,
      module: 'system_dm',  // Fixed module name as specified
      audio: enableAudio.value  // Use the audio toggle switch value
    }

    // Send the message via the Neuro Sama chat WebSocket connection
    await connectionStore.sendNeuroSamaChatMessage(message)

  } catch (error) {
    console.error('Failed to send chat message:', error)
    alert('Failed to send message: ' + (error instanceof Error ? error.message : 'Unknown error'))
  } finally {
    // Loading state will be reset when completion message is received
  }
}

onMounted(() => {
  // Add event listener for Neuro Sama WebSocket messages
  if (connectionStore.neuroSamaWs) {
    connectionStore.neuroSamaWs.addEventListener('message', handleMessageEvent)
  }

  // Set up chat message callback
  connectionStore.onChatMessage = handleChatMessage;

  // Load initial data
  loadData()
})

onUnmounted(() => {
  // Remove event listeners
  if (connectionStore.neuroSamaWs) {
    connectionStore.neuroSamaWs.removeEventListener('message', handleMessageEvent)
  }

  // Clear chat message callback
  connectionStore.onChatMessage = null;

  // Stop any playing audio
  if (currentPlayingAudio.value) {
    const audioElement = document.getElementById(`audio_${currentPlayingAudio.value}`) as HTMLAudioElement
    if (audioElement) {
      audioElement.pause()
      audioElement.currentTime = 0
    }
  }

  // Clear audio queue
  neuroAudioQueue.value = []
  currentPlayingAudio.value = null
})
</script>

<style scoped>
.font-mono {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>