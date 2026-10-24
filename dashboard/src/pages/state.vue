<template>
  <div class="state-page">
    <v-card variant="outlined" class="mb-4">
      <v-card-text class="d-flex align-center flex-wrap ga-3">
        <div class="text-h6 mr-2">State Machines</div>

        <v-btn-toggle v-model="mode" mandatory density="compact" variant="outlined" divided>
          <v-btn value="both" size="small">Both</v-btn>
          <v-btn value="neuro_sama" size="small">Neuro</v-btn>
          <v-btn value="stream" size="small">Stream</v-btn>
        </v-btn-toggle>

        <v-spacer />

        <span v-if="neuroStatus" class="text-caption text-medium-emphasis">
          Neuro rev {{ neuroStatus.rev }} · {{ relativeTime(neuroStatus.ts) }}
        </span>
        <span v-if="streamStatus" class="text-caption text-medium-emphasis">
          Stream rev {{ streamStatus.rev }} · {{ relativeTime(streamStatus.ts) }}
        </span>
        <span v-if="state" class="text-caption text-disabled">
          vedal {{ relativeTime(state.vedal.ts) }}
        </span>
        <v-switch
          v-model="auto"
          label="Auto refresh"
          color="primary"
          density="compact"
          hide-details
          class="mt-1"
        />
        <v-btn
          size="small"
          variant="tonal"
          prepend-icon="mdi-refresh"
          :loading="loading"
          @click="refresh"
        >
          Refresh
        </v-btn>
      </v-card-text>
      <v-progress-linear v-if="loading" indeterminate color="primary" />
    </v-card>

    <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mb-4">
      {{ error }}
    </v-alert>

    <v-row dense>
      <!-- Neuro Sama -->
      <v-col v-if="mode !== 'stream'" cols="12" md="6">
        <v-card variant="outlined" class="h-100">
          <v-card-title class="d-flex align-center text-subtitle-1">
            <v-icon icon="mdi-brain" class="mr-2" />
            Neuro Sama
            <v-spacer />
            <v-chip
              v-if="neuro"
              size="x-small"
              :color="neuro.reachable ? 'success' : 'error'"
              variant="tonal"
            >
              {{ neuro.reachable ? 'reachable' : 'unreachable' }}
            </v-chip>
          </v-card-title>

          <v-card-subtitle v-if="neuroStatus" class="d-flex align-center flex-wrap ga-2">
            <span>rev {{ neuroStatus.rev }}</span>
            <span>{{ neuroStatus.ts }}</span>
            <v-chip
              v-if="neuro && neuro.elapsed_ms > 1500"
              size="x-small"
              color="warning"
              variant="tonal"
            >
              {{ neuro.elapsed_ms }}ms
            </v-chip>
          </v-card-subtitle>

          <v-card-text>
            <template v-if="neuro">
              <ModuleOffline
                v-if="!neuro.reachable"
                module-name="neuro_sama"
                :on-started="refresh"
              />
              <template v-else-if="neuroStatus">
                <StateMachineGraph
                  v-for="view in neuroGraphs"
                  :key="view.key"
                  :graph="view.graph"
                  :current="view.current"
                  :title="view.title"
                  class="mb-5"
                />
                <StateSections :sections="neuroStatus.sections" />
              </template>
              <v-alert v-else type="warning" variant="tonal" density="compact">
                No status envelope received.
              </v-alert>
            </template>
            <v-alert v-else type="warning" variant="tonal" density="compact">
              neuro_sama 不在 /manage/state 聚合结果里（config 未配置该模块？）
            </v-alert>
          </v-card-text>
        </v-card>
      </v-col>

      <!-- stream -->
      <v-col v-if="mode !== 'neuro_sama'" cols="12" md="6">
        <v-card variant="outlined" class="h-100">
          <v-card-title class="d-flex align-center text-subtitle-1">
            <v-icon icon="mdi-broadcast" class="mr-2" />
            Stream
            <v-spacer />
            <v-chip
              v-if="stream"
              size="x-small"
              :color="stream.reachable ? 'success' : 'error'"
              variant="tonal"
            >
              {{ stream.reachable ? 'reachable' : 'unreachable' }}
            </v-chip>
          </v-card-title>

          <v-card-subtitle v-if="streamStatus" class="d-flex align-center flex-wrap ga-2">
            <span>rev {{ streamStatus.rev }}</span>
            <span>{{ streamStatus.ts }}</span>
            <v-chip
              v-if="stream && stream.elapsed_ms > 1500"
              size="x-small"
              color="warning"
              variant="tonal"
            >
              {{ stream.elapsed_ms }}ms
            </v-chip>
          </v-card-subtitle>

          <v-card-text>
            <template v-if="stream">
              <ModuleOffline v-if="!stream.reachable" module-name="stream" :on-started="refresh" />
              <template v-else-if="streamStatus">
                <StateMachineGraph
                  v-for="view in streamGraphs"
                  :key="view.key"
                  :graph="view.graph"
                  :current="view.current"
                  :title="view.title"
                  class="mb-5"
                />
                <StateSections :sections="streamStatus.sections" />
              </template>
              <v-alert v-else type="warning" variant="tonal" density="compact">
                No status envelope received.
              </v-alert>
            </template>
            <v-alert v-else type="warning" variant="tonal" density="compact">
              stream 不在 /manage/state 聚合结果里（config 未配置该模块？）
            </v-alert>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import ModuleOffline from '@/components/ModuleOffline.vue'
import StateMachineGraph from '@/components/state/StateMachineGraph.vue'
import StateSections from '@/components/state/StateSections.vue'
import { relativeTime, useModuleState } from '@/composables/useModuleState'
import { neuroModule, streamModule } from '@/types/status'
import type { GamesStatus, StateGraph } from '@/types/status'

type ModuleChoice = 'both' | 'neuro_sama' | 'stream'

interface GraphView {
  key: string
  title: string
  graph: StateGraph
  current: string
}

const { state, loading, error, auto, refresh, start, stop } = useModuleState()

const mode = ref<ModuleChoice>('both')

const neuro = computed(() => neuroModule(state.value))
const stream = computed(() => streamModule(state.value))
const neuroStatus = computed(() => neuro.value?.status ?? null)
const streamStatus = computed(() => stream.value?.status ?? null)

const neuroGraphs = computed<GraphView[]>(() => {
  const sections = neuroStatus.value?.sections
  const out: GraphView[] = []
  if (sections?.scene.graph) {
    out.push({
      key: 'scene',
      title: 'Scene',
      graph: sections.scene.graph,
      current: sections.scene.current?.id ?? '',
    })
  }
  return out
})

const streamGraphs = computed<GraphView[]>(() => {
  const sections = streamStatus.value?.sections
  const out: GraphView[] = []
  if (sections?.loop.graph) {
    out.push({
      key: 'loop',
      title: 'Loop',
      graph: sections.loop.graph,
      current: sections.loop.current?.state ?? '',
    })
  }
  if (sections?.scene.graph) {
    out.push({
      key: 'scene',
      title: 'Scene',
      graph: sections.scene.graph,
      current: sections.scene.current?.id ?? '',
    })
  }
  if (sections?.games.graph) {
    out.push({
      key: 'games',
      title: 'Games',
      graph: sections.games.graph,
      current: gameCurrent(sections.games.current),
    })
  }
  return out
})

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** games.graph 的 state id 带游戏前缀：`<game>.<phase>`。 */
function gameCurrent(games: GamesStatus | null): string {
  if (!games?.active) return ''
  const entry = games[games.active]
  if (!isRecord(entry) || typeof entry.phase !== 'string') return ''
  return `${games.active}.${entry.phase}`
}

onMounted(start)
onUnmounted(stop)
</script>

<style scoped>
.state-page {
  padding-bottom: 32px;
}
</style>
