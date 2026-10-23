<template>
  <div class="state-machine">
    <div class="d-flex align-center mb-2">
      <div class="text-subtitle-2">{{ title }}</div>
      <v-spacer />
      <span v-if="current" class="text-caption text-medium-emphasis">current: {{ current }}</span>
    </div>

    <template v-if="!graph">
      <div class="text-body-2 text-disabled">No state graph declared.</div>
    </template>

    <template v-else>
      <div v-if="!graph.states.length && !current" class="text-caption text-disabled">
        No states declared.
      </div>
      <div v-else class="state-chips">
        <v-tooltip
          v-for="state in graph.states"
          :key="state.id"
          :text="state.desc || ''"
          :disabled="!state.desc"
          location="top"
        >
          <template #activator="{ props: tipProps }">
            <v-chip
              v-bind="tipProps"
              size="small"
              :color="state.id === current ? 'primary' : undefined"
              :variant="state.id === current ? 'flat' : 'outlined'"
              :prepend-icon="state.id === current ? 'mdi-record' : undefined"
              :class="{ 'state-chip--current': state.id === current }"
            >
              {{ state.label || state.id }}
            </v-chip>
          </template>
        </v-tooltip>

        <!-- 投影与清单漂移：current 不在 states 里时不能静默吞掉 -->
        <v-tooltip
          v-if="current && !currentDeclared"
          text="current state is not declared in graph.states (catalog drift)"
          location="top"
        >
          <template #activator="{ props: tipProps }">
            <v-chip
              v-bind="tipProps"
              size="small"
              color="error"
              variant="flat"
              prepend-icon="mdi-alert"
            >
              Undeclared: {{ current }}
            </v-chip>
          </template>
        </v-tooltip>
      </div>

      <div v-if="graph.transitions.length" class="mt-3">
        <div class="text-caption text-disabled mb-1">Transitions</div>
        <div
          v-for="(transition, index) in sortedTransitions"
          :key="`${transition.from}->${transition.to}-${index}`"
          class="transition-row"
          :class="{ 'transition-row--active': transition.from === current }"
        >
          <span class="mono">{{ transition.from }}</span>
          <span class="mx-1">→</span>
          <span class="mono">{{ transition.to }}</span>
          <span v-if="transition.by" class="transition-by">{{ transition.by }}</span>
        </div>
      </div>
    </template>
  </div>
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import type { StateGraph } from '@/types/status'

const props = defineProps<{
  graph?: StateGraph | null
  current: string
  title: string
}>()

const currentDeclared = computed(
  () => !props.current || (props.graph?.states.some((s) => s.id === props.current) ?? false),
)

/**
 * 与当前状态相关的转移排前面：消费者最关心"我现在能去哪"。
 * 数组很短（手写小表），稳定排序即可。
 */
const sortedTransitions = computed(() => {
  const list = [...(props.graph?.transitions ?? [])]
  return list.sort(
    (a, b) => Number(b.from === props.current) - Number(a.from === props.current),
  )
})
</script>

<style scoped>
.state-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.state-chip--current {
  font-weight: 700;
}
.transition-row {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
  color: rgb(var(--v-theme-on-surface-variant));
}
.transition-row--active {
  background: rgba(var(--v-theme-primary), 0.1);
  color: rgb(var(--v-theme-primary));
  font-weight: 600;
}
.transition-by {
  margin-left: 8px;
  font-size: 11px;
  opacity: 0.65;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
</style>
