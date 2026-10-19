<template>
  <div class="memory-redirect">
    <v-progress-circular indeterminate size="32" width="3" />
  </div>
</template>

<script lang="ts" setup>
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'

// 旧路由兼容：Memory 已合并进 Persona 页的 Short-term Memory tab
const router = useRouter()
onMounted(() => router.replace('/neuro-sama-persona'))
</script>

<style scoped>
.memory-redirect {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 50vh;
}
</style>
