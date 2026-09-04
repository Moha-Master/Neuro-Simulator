<template>
  <div class="module-offline">
    <v-icon size="72" color="grey-lighten-1">mdi-robot-off</v-icon>
    <div class="text-h6 mt-4">{{ moduleName }} 模块未运行</div>
    <div class="text-body-2 text-grey-darken-1 mt-1">
      请先启动该模块后再使用此页面。
    </div>
    <div class="d-flex gap-2 mt-6">
      <v-btn color="primary" variant="tonal" :loading="starting" @click="start">
        <v-icon left>mdi-play</v-icon> 启动模块
      </v-btn>
      <v-btn variant="text" to="/">
        <v-icon left>mdi-home</v-icon> 返回 Home
      </v-btn>
    </div>
    <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mt-6" closable @click:close="error = ''">
      {{ error }}
    </v-alert>
  </div>
</template>

<script lang="ts" setup>
import { ref } from 'vue'
import { api, describeError } from '@/stores/connection'

const props = defineProps<{
  moduleName: string
  /** 启动成功后回调（用于立即重查状态，父页面据此切回正常 UI） */
  onStarted?: () => void
}>()

const starting = ref(false)
const error = ref('')

async function start() {
  starting.value = true
  error.value = ''
  try {
    // module_run 会等待健康检查通过后再返回，故返回即可重查
    await api(`/manage/module_run/${props.moduleName}`, { method: 'POST' })
    props.onStarted?.()
  } catch (e) {
    error.value = '启动失败: ' + describeError(e)
  } finally {
    starting.value = false
  }
}
</script>

<style scoped>
.module-offline {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 24px;
}
</style>
