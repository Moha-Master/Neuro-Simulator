<template>
  <div class="sessions-page">
    <div v-if="!status.checked.value" class="sessions-loading">
      <v-progress-circular indeterminate size="48" width="4" />
    </div>

    <ModuleOffline
      v-else-if="!status.reachable.value"
      module-name="stream"
      :on-started="status.check"
    />

    <template v-else>
      <v-alert
        v-if="st.error.value"
        type="error"
        variant="tonal"
        density="compact"
        closable
        class="mb-4"
        @click:close="st.error.value = ''"
      >
        {{ st.error.value }}
      </v-alert>

      <v-card variant="outlined">
        <v-card-title class="text-subtitle-1 d-flex align-center">
          场次
          <v-spacer />
          <v-btn
            size="small"
            variant="text"
            prepend-icon="mdi-refresh"
            :loading="st.busy.value"
            @click="refresh"
          >
            刷新
          </v-btn>
        </v-card-title>
        <v-card-text class="text-caption text-disabled pb-0">
          更详细的场次信息（回放队列、轮次记录）：即将推出
        </v-card-text>
        <v-table v-if="st.sessions.value.length" density="comfortable">
          <thead>
            <tr>
              <th class="text-left">Session</th>
              <th class="text-left">状态</th>
              <th class="text-left">模式</th>
              <th class="text-left">开始</th>
              <th class="text-left">结束</th>
              <th class="text-left">轮次</th>
              <th class="text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in st.sessions.value" :key="s.session_id">
              <td class="text-body-2">{{ short(s.session_id) }}</td>
              <td>
                <v-chip
                  size="x-small"
                  :color="s.state === 'offline' ? 'grey' : 'success'"
                  variant="tonal"
                >
                  {{ s.state }}
                </v-chip>
              </td>
              <td>
                <v-chip
                  size="x-small"
                  :color="s.recording ? 'warning' : 'primary'"
                  variant="tonal"
                >
                  {{ s.recording ? '模拟' : '直播' }}
                </v-chip>
              </td>
              <td class="text-body-2">{{ s.started_at }}</td>
              <td class="text-body-2">{{ s.ended_at ?? '—' }}</td>
              <td class="text-body-2">{{ s.rounds }}</td>
              <td class="text-right">
                <v-btn
                  icon="mdi-delete-outline"
                  size="x-small"
                  color="error"
                  variant="text"
                  title="删除场次及消息队列"
                  @click="handleDelete(s.session_id)"
                />
              </td>
            </tr>
          </tbody>
        </v-table>
        <v-card-text v-else class="text-disabled">暂无场次记录。</v-card-text>
      </v-card>
    </template>
  </div>
</template>

<script lang="ts" setup>
import { onMounted } from 'vue'
import ModuleOffline from '@/components/ModuleOffline.vue'
import { useModuleStatus } from '@/composables/useModuleStatus'
import { useStream } from '@/composables/useStream'

const status = useModuleStatus('stream')
const st = useStream()

function short(id: string): string {
  return id ? id.slice(0, 8) : '—'
}

async function refresh(): Promise<void> {
  await Promise.all([st.refreshSessions(), st.refreshStatus()])
}

async function handleDelete(sessionId: string): Promise<void> {
  if (!confirm(`确定要删除场次 ${short(sessionId)} 及其对应的消息队列和 Neuro 对话吗？`)) return
  await st.deleteSession(sessionId)
}

onMounted(() => {
  void refresh()
})
</script>

<style scoped>
.sessions-page {
  max-width: 1100px;
  margin: 0 auto;
  padding: 8px 0 32px;
}

.sessions-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 50vh;
}
</style>
