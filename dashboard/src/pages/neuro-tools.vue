<template>
  <div class="neuro-tools">
    <div v-if="!status.checked.value" class="neuro-tools__fill">
      <v-progress-circular indeterminate size="48" width="4" />
    </div>

    <ModuleOffline
      v-else-if="!status.reachable.value"
      module-name="neuro_sama"
      :on-started="status.check"
    />

    <template v-else>
      <div class="neuro-tools__toolbar">
        <div class="d-flex align-center ga-2">
          <v-icon color="primary">mdi-tools</v-icon>
          <span class="text-subtitle-1 font-weight-medium">Tools</span>
          <v-chip size="small" variant="tonal">{{ enabledCount }}/{{ tools.length }} enabled</v-chip>
        </div>
        <v-spacer />
        <v-btn
          size="small"
          variant="text"
          prepend-icon="mdi-refresh"
          :loading="loading"
          @click="loadTools"
        >
          刷新
        </v-btn>
      </div>

      <v-row dense>
        <!-- ============ 工具列表 ============ -->
        <v-col cols="12" lg="7">
          <v-card variant="outlined" density="compact" class="neuro-tools__card">
            <v-card-title class="d-flex align-center text-body-2 font-weight-medium">
              工具
              <v-spacer />
              <span class="text-caption text-medium-emphasis">
                timeout {{ toolTimeout }}s
              </span>
            </v-card-title>
            <v-card-text class="pt-0">
              <div v-if="!tools.length" class="text-caption text-medium-emphasis">没有工具。</div>
              <v-list v-else density="compact" class="neuro-tools__list">
                <v-list-item v-for="tool in tools" :key="tool.name" class="neuro-tools__row">
                  <template #prepend>
                    <v-icon size="small">mdi-puzzle-outline</v-icon>
                  </template>
                  <v-list-item-title class="d-flex align-center ga-2 flex-wrap">
                    <span
                      class="font-mono text-body-2"
                      :class="{ 'neuro-tools__name--off': !tool.enabled }"
                    >{{ tool.name }}</span>
                    <v-chip
                      size="x-small"
                      variant="tonal"
                      :color="originColor(tool)"
                    >
                      {{ originLabel(tool) }}
                    </v-chip>
                  </v-list-item-title>
                  <v-list-item-subtitle class="text-caption neuro-tools__desc">
                    {{ tool.description }}
                  </v-list-item-subtitle>
                  <template #append>
                    <div class="d-flex align-center ga-1">
                      <v-btn
                        size="x-small"
                        variant="text"
                        prepend-icon="mdi-play"
                        @click="openRun(tool)"
                      >
                        运行
                      </v-btn>
                      <v-switch
                        :model-value="tool.enabled"
                        color="primary"
                        density="compact"
                        hide-details
                        inset
                        :loading="toggling === tool.name"
                        @update:model-value="toggleTool(tool, $event as boolean)"
                      />
                    </div>
                  </template>
                </v-list-item>
              </v-list>
              <div class="text-caption text-medium-emphasis mt-1">
                禁用只影响暴露给 LLM 的 schema（下一步生成即生效）；
                /health 与「运行」仍看全量。手动运行是调试通道，不受开关限制。
              </div>
            </v-card-text>
          </v-card>
        </v-col>

        <!-- ============ MCP Servers ============ -->
        <v-col cols="12" lg="5">
          <v-card variant="outlined" density="compact" class="neuro-tools__card">
            <v-card-title class="d-flex align-center text-body-2 font-weight-medium">
              MCP Servers
              <v-spacer />
              <v-btn size="x-small" variant="flat" color="primary" @click="openMcpDialog(null)">
                <v-icon start size="small">mdi-plus</v-icon> 添加
              </v-btn>
            </v-card-title>
            <v-card-text class="pt-0">
              <div v-if="!mcpServers.length" class="text-caption text-medium-emphasis">未配置</div>
              <div v-for="server in mcpServers" :key="server.name" class="neuro-tools__mcp">
                <div class="d-flex align-center ga-2 flex-wrap">
                  <span class="font-mono text-body-2">{{ server.name }}</span>
                  <v-chip size="x-small" variant="tonal">
                    <v-icon start size="x-small">{{ transportIcon(server.transport) }}</v-icon>
                    {{ server.transport }}
                  </v-chip>
                  <v-tooltip
                    v-if="server.error"
                    location="top"
                    :text="server.error"
                  >
                    <template #activator="{ props }">
                      <v-chip v-bind="props" size="x-small" color="error" variant="tonal">
                        error
                      </v-chip>
                    </template>
                  </v-tooltip>
                  <v-chip
                    v-else-if="!server.enabled"
                    size="x-small"
                    variant="tonal"
                  >
                    未启用
                  </v-chip>
                  <v-chip
                    v-else-if="server.connected"
                    size="x-small"
                    color="success"
                    variant="tonal"
                  >
                    connected
                  </v-chip>
                  <v-chip v-else size="x-small" variant="tonal">未连接</v-chip>
                  <span class="text-caption text-medium-emphasis">
                    {{ server.tool_count }} tool(s)
                  </span>
                  <v-spacer />
                  <v-switch
                    :model-value="server.enabled"
                    color="primary"
                    density="compact"
                    hide-details
                    inset
                    :loading="mcpBusy === server.name"
                    @update:model-value="toggleMcp(server, $event as boolean)"
                  />
                </div>
                <div class="text-caption text-medium-emphasis neuro-tools__mcp-detail">
                  {{ server.transport === 'stdio'
                    ? `${server.command || ''} ${(server.args || []).join(' ')}`
                    : server.url }}
                </div>
                <div class="d-flex ga-1 mt-1">
                  <v-btn size="x-small" variant="text" @click="openMcpDialog(server)">编辑</v-btn>
                  <v-btn
                    size="x-small"
                    variant="text"
                    color="error"
                    @click="deleteMcp(server)"
                  >
                    删除
                  </v-btn>
                </div>
              </div>
              <div class="text-caption text-medium-emphasis mt-2">
                改动会即时重连 MCP（含所有 server 重建 registry），直播进行中慎改。
              </div>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>

      <!-- ============ 手动运行 ============ -->
      <v-dialog v-model="runDialog" max-width="640" persistent>
        <v-card v-if="runTarget">
          <v-card-title class="d-flex align-center ga-2">
            <span class="font-mono">{{ runTarget.name }}</span>
            <v-chip v-if="!runTarget.enabled" size="x-small" color="warning" variant="tonal">
              已禁用
            </v-chip>
          </v-card-title>
          <v-card-text>
            <v-alert
              v-if="!runTarget.enabled"
              type="info"
              variant="tonal"
              density="compact"
              class="mb-3"
            >
              该工具已禁用，仅 LLM 不可见，手动运行不受限制。
            </v-alert>
            <div class="text-caption text-medium-emphasis mb-1">arguments（JSON 对象）</div>
            <v-textarea
              v-model="runArguments"
              variant="outlined"
              density="compact"
              rows="6"
              auto-grow
              class="font-mono"
              spellcheck="false"
            />
            <div v-if="argumentsError" class="text-caption text-error">{{ argumentsError }}</div>

            <template v-if="runResult">
              <v-divider class="my-3" />
              <div class="d-flex align-center ga-2 mb-1">
                <v-chip
                  size="small"
                  :color="runResult.ok ? 'success' : 'error'"
                  variant="tonal"
                >
                  {{ runResult.ok ? 'ok' : 'error' }}
                </v-chip>
                <span class="text-caption text-medium-emphasis">{{ runResult.duration_ms }} ms</span>
              </div>
              <pre class="neuro-tools__result">{{ runResult.result }}</pre>
            </template>
          </v-card-text>
          <v-card-actions>
            <v-spacer />
            <v-btn variant="text" @click="runDialog = false">关闭</v-btn>
            <v-btn
              color="primary"
              variant="flat"
              :loading="running"
              :disabled="!!argumentsError"
              @click="runTool"
            >
              运行
            </v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>

      <!-- ============ MCP 编辑 ============ -->
      <v-dialog v-model="mcpDialog" max-width="560" persistent>
        <v-card v-if="mcpDraft">
          <v-card-title>{{ mcpEditing ? '编辑' : '添加' }} MCP Server</v-card-title>
          <v-card-text>
            <v-row dense>
              <v-col cols="12" sm="6">
                <v-text-field
                  v-model="mcpDraft.name"
                  label="Name（工具前缀）"
                  variant="outlined"
                  density="compact"
                  :disabled="!!mcpEditing"
                />
              </v-col>
              <v-col cols="12" sm="6">
                <v-select
                  v-model="mcpDraft.transport"
                  :items="[
                    { title: 'stdio（本地命令）', value: 'stdio' },
                    { title: 'streamable HTTP', value: 'http' },
                    { title: 'SSE（legacy）', value: 'sse' },
                  ]"
                  label="Transport"
                  variant="outlined"
                  density="compact"
                />
              </v-col>
              <template v-if="mcpDraft.transport === 'stdio'">
                <v-col cols="12">
                  <v-text-field
                    v-model="mcpDraft.command"
                    label="Command（如 npx）"
                    variant="outlined"
                    density="compact"
                  />
                </v-col>
                <v-col cols="12">
                  <v-textarea
                    v-model="mcpDraft.argsRaw"
                    label="Args（每行一个参数）"
                    variant="outlined"
                    density="compact"
                    rows="2"
                    auto-grow
                    class="font-mono"
                  />
                </v-col>
                <v-col cols="12">
                  <v-textarea
                    v-model="mcpDraft.envRaw"
                    label="Env（每行 KEY=VALUE，可空）"
                    variant="outlined"
                    density="compact"
                    rows="2"
                    auto-grow
                    class="font-mono"
                  />
                </v-col>
              </template>
              <template v-else>
                <v-col cols="12">
                  <v-text-field
                    v-model="mcpDraft.url"
                    label="URL（如 http://127.0.0.1:9000/mcp）"
                    variant="outlined"
                    density="compact"
                  />
                </v-col>
                <v-col cols="12">
                  <v-textarea
                    v-model="mcpDraft.headersRaw"
                    label="Headers（每行 KEY: VALUE）"
                    hint="示例：Authorization: Bearer <token>；只按每行第一个冒号拆分，值内冒号保留"
                    persistent-hint
                    variant="outlined"
                    density="compact"
                    rows="2"
                    auto-grow
                    class="font-mono"
                    spellcheck="false"
                  />
                </v-col>
              </template>
              <v-col cols="12">
                <v-switch
                  v-model="mcpDraft.enabled"
                  label="启用"
                  color="primary"
                  density="compact"
                  hide-details
                  inset
                />
              </v-col>
            </v-row>
            <div class="text-caption text-medium-emphasis mt-2">
              保存会立即落库并重连 MCP；直播进行中慎操作。
            </div>
          </v-card-text>
          <v-card-actions>
            <v-spacer />
            <v-btn variant="text" @click="mcpDialog = false">取消</v-btn>
            <v-btn color="primary" variant="flat" :loading="savingMcp" @click="saveMcp">
              确定
            </v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>
    </template>

    <v-snackbar v-model="snackbar" :timeout="4000" location="bottom">
      {{ snackbarText }}
    </v-snackbar>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, ref } from 'vue'
import ModuleOffline from '@/components/ModuleOffline.vue'
import { useModuleStatus } from '@/composables/useModuleStatus'
import { api, describeError } from '@/stores/connection'
import type {
  McpServerInfo,
  McpServerInput,
  McpTransport,
  ToolInfo,
  ToolParameters,
  ToolRunResponse,
  ToolsMutationResponse,
  ToolsResponse,
} from '@/types/tools'

const BASE = '/manage/neuro_sama/manage'

const status = useModuleStatus('neuro_sama')

const tools = ref<ToolInfo[]>([])
const mcpServers = ref<McpServerInfo[]>([])
const toolTimeout = ref(0)
const loading = ref(false)
const toggling = ref('')
const mcpBusy = ref('')
const snackbar = ref(false)
const snackbarText = ref('')

const enabledCount = computed(() => tools.value.filter((t) => t.enabled).length)

// ---------- 加载 ----------

function apply(data: ToolsResponse): void {
  tools.value = data.tools
  mcpServers.value = data.mcp_servers
  toolTimeout.value = data.tool_timeout_s
}

function notify(message: string): void {
  snackbarText.value = message
  snackbar.value = true
}

async function loadTools(): Promise<void> {
  loading.value = true
  try {
    apply(await api<ToolsResponse>(`${BASE}/tools`))
  } catch (e) {
    notify(`读取工具失败：${describeError(e)}`)
  } finally {
    loading.value = false
  }
}

// ---------- 展示 ----------

function originLabel(tool: ToolInfo): string {
  if (tool.game) return `game:${tool.game}`
  return tool.origin
}

function originColor(tool: ToolInfo): string {
  if (tool.game) return 'deep-purple'
  if (tool.origin === 'mcp') return 'teal'
  return 'blue-grey'
}

function transportIcon(transport: McpTransport): string {
  if (transport === 'stdio') return 'mdi-console'
  if (transport === 'sse') return 'mdi-wifi'
  return 'mdi-web'
}

// ---------- 工具开关 ----------

async function toggleTool(tool: ToolInfo, enabled: boolean): Promise<void> {
  toggling.value = tool.name
  try {
    apply(
      await api<ToolsMutationResponse>(
        `${BASE}/tools/${encodeURIComponent(tool.name)}/enabled`,
        { method: 'PUT', body: JSON.stringify({ enabled }) },
      ),
    )
  } catch (e) {
    notify(`切换失败：${describeError(e)}`)
  } finally {
    toggling.value = ''
  }
}

// ---------- 手动运行 ----------

const runDialog = ref(false)
const runTarget = ref<ToolInfo | null>(null)
const runArguments = ref('{}')
const runResult = ref<ToolRunResponse | null>(null)
const running = ref(false)

const argumentsError = computed(() => {
  try {
    const parsed = JSON.parse(runArguments.value)
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return 'arguments 必须是 JSON 对象'
    }
    return ''
  } catch (e) {
    return `JSON 解析失败：${e instanceof Error ? e.message : String(e)}`
  }
})

/** 按 parameters schema 生成脚手架：只填 required，对象递归一层。 */
function scaffold(params: ToolParameters | undefined, depth = 0): Record<string, unknown> {
  const required = Array.isArray(params?.required) ? params.required : []
  if (!required.length) return {}
  const properties = params?.properties ?? {}
  const out: Record<string, unknown> = {}
  for (const key of required) {
    out[key] = scaffoldValue(properties[key], depth)
  }
  return out
}

function scaffoldValue(schema: ToolParameters | undefined, depth: number): unknown {
  switch (schema?.type) {
    case 'string':
      return ''
    case 'number':
    case 'integer':
      return 0
    case 'boolean':
      return false
    case 'array':
      return []
    case 'object':
      // 递归一层即可：更深层的对象给空对象占位
      return depth >= 1 ? {} : scaffold(schema, depth + 1)
    default:
      return null
  }
}

function openRun(tool: ToolInfo): void {
  runTarget.value = tool
  runArguments.value = JSON.stringify(scaffold(tool.parameters), null, 2)
  runResult.value = null
  runDialog.value = true
}

async function runTool(): Promise<void> {
  if (!runTarget.value || argumentsError.value) return
  running.value = true
  try {
    runResult.value = await api<ToolRunResponse>(
      `${BASE}/tools/${encodeURIComponent(runTarget.value.name)}/run`,
      { method: 'POST', body: JSON.stringify({ arguments: JSON.parse(runArguments.value) }) },
    )
  } catch (e) {
    runResult.value = { ok: false, result: describeError(e), duration_ms: 0 }
  } finally {
    running.value = false
  }
}

// ---------- MCP ----------

interface McpDraft {
  name: string
  transport: McpTransport
  command: string
  argsRaw: string
  envRaw: string
  url: string
  headersRaw: string
  enabled: boolean
}

const mcpDialog = ref(false)
const mcpEditing = ref<string | null>(null)
const mcpDraft = ref<McpDraft | null>(null)
const savingMcp = ref(false)

function openMcpDialog(server: McpServerInfo | null): void {
  mcpEditing.value = server?.name ?? null
  mcpDraft.value = {
    name: server?.name ?? '',
    transport: server?.transport ?? 'stdio',
    command: server?.command ?? '',
    argsRaw: (server?.args ?? []).join('\n'),
    envRaw: Object.entries(server?.env ?? {})
      .map(([k, v]) => `${k}=${v}`)
      .join('\n'),
    url: server?.url ?? '',
    headersRaw: Object.entries(server?.headers ?? {})
      .map(([k, v]) => `${k}: ${v}`)
      .join('\n'),
    enabled: server?.enabled ?? true,
  }
  mcpDialog.value = true
}

/** 逐行 KEY: VALUE；只按每行第一个冒号拆分（值内冒号保留）。 */
function parseHeaders(raw: string): Record<string, string> {
  const out: Record<string, string> = {}
  raw.split('\n').forEach((rawLine, index) => {
    const line = rawLine.trim()
    if (!line) return
    const i = line.indexOf(':')
    if (i < 0) {
      throw new Error(`Headers 第 ${index + 1} 行缺少 ':'`)
    }
    const key = line.slice(0, i).trim()
    if (!key) {
      throw new Error(`Headers 第 ${index + 1} 行的键为空`)
    }
    out[key] = line.slice(i + 1).trim()
  })
  return out
}

function draftPayload(draft: McpDraft): McpServerInput {
  const payload: McpServerInput = {
    name: draft.name.trim(),
    transport: draft.transport,
    enabled: draft.enabled,
  }
  if (draft.transport === 'stdio') {
    payload.command = draft.command.trim()
    const args = draft.argsRaw
      .split('\n')
      .map((x) => x.trim())
      .filter(Boolean)
    if (args.length) payload.args = args
    const env: Record<string, string> = {}
    for (const line of draft.envRaw.split('\n').map((x) => x.trim()).filter(Boolean)) {
      const i = line.indexOf('=')
      if (i > 0) env[line.slice(0, i)] = line.slice(i + 1)
    }
    if (Object.keys(env).length) payload.env = env
  } else {
    payload.url = draft.url.trim()
    const headers = parseHeaders(draft.headersRaw)
    if (Object.keys(headers).length) payload.headers = headers
  }
  return payload
}

async function saveMcp(): Promise<void> {
  const draft = mcpDraft.value
  if (!draft) return
  let body: string
  try {
    body = JSON.stringify(draftPayload(draft))
  } catch (e) {
    notify(e instanceof Error ? e.message : String(e))
    return
  }
  savingMcp.value = true
  try {
    const editing = mcpEditing.value
    apply(
      await api<ToolsMutationResponse>(
        editing === null
          ? `${BASE}/mcp-servers`
          : `${BASE}/mcp-servers/${encodeURIComponent(editing)}`,
        { method: editing === null ? 'POST' : 'PUT', body },
      ),
    )
    mcpDialog.value = false
  } catch (e) {
    notify(`保存 MCP server 失败：${describeError(e)}`)
  } finally {
    savingMcp.value = false
  }
}

async function toggleMcp(server: McpServerInfo, enabled: boolean): Promise<void> {
  mcpBusy.value = server.name
  try {
    apply(
      await api<ToolsMutationResponse>(
        `${BASE}/mcp-servers/${encodeURIComponent(server.name)}/enabled`,
        { method: 'PUT', body: JSON.stringify({ enabled }) },
      ),
    )
  } catch (e) {
    notify(`切换 MCP server 失败：${describeError(e)}`)
  } finally {
    mcpBusy.value = ''
  }
}

async function deleteMcp(server: McpServerInfo): Promise<void> {
  if (!window.confirm(`删除 MCP server ${server.name}？`)) return
  mcpBusy.value = server.name
  try {
    apply(
      await api<ToolsMutationResponse>(
        `${BASE}/mcp-servers/${encodeURIComponent(server.name)}`,
        { method: 'DELETE' },
      ),
    )
  } catch (e) {
    notify(`删除 MCP server 失败：${describeError(e)}`)
  } finally {
    mcpBusy.value = ''
  }
}

onMounted(() => {
  void loadTools()
})
</script>

<style scoped>
.neuro-tools {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 20px 20px;
}

.neuro-tools__fill {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 40vh;
}

.neuro-tools__toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.neuro-tools__card {
  height: 100%;
}

.neuro-tools__row {
  align-items: flex-start;
}

.neuro-tools__desc {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.neuro-tools__name--off {
  color: rgb(var(--v-theme-on-surface));
  opacity: 0.45;
  text-decoration: line-through;
}

.neuro-tools__mcp {
  padding: 8px 0;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}

.neuro-tools__mcp-detail {
  overflow-wrap: anywhere;
}

.neuro-tools__result {
  margin: 0;
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.8rem;
  padding: 8px;
  border-radius: 6px;
  background: rgba(var(--v-border-color), 0.08);
}
</style>
