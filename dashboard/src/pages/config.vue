<template>
  <v-container fluid>
    <v-row justify="center">
      <v-col cols="12" md="11" lg="10">
        <v-card variant="outlined">
          <v-card-title class="d-flex align-center pa-4 pb-2">
            <v-icon class="mr-2">mdi-tune-variant</v-icon>
            Configuration
            <v-spacer />
            <v-chip v-if="dirtyCount > 0" size="small" color="warning" variant="tonal" class="mr-3">
              {{ dirtyCount }} unsaved section{{ dirtyCount > 1 ? 's' : '' }}
            </v-chip>
            <v-btn size="small" variant="text" :disabled="busy" @click="loadConfig">
              <v-icon start>mdi-refresh</v-icon>
              重新读取
            </v-btn>
          </v-card-title>

          <v-card-text>
            <v-alert v-if="msg" :type="msgType" variant="tonal" density="compact" closable class="mb-3" @click:close="msg = ''">
              {{ msg }}
            </v-alert>
            <v-progress-linear v-if="busy" indeterminate class="mb-2" />

            <template v-if="cfg">
              <v-row no-gutters>
                <v-col cols="12" sm="3" md="2" class="pr-sm-2">
                  <v-tabs v-model="tab" direction="vertical" density="comfortable" slider-size="0">
                    <v-tab value="services" :prepend-icon="dirty.has('server') ? 'mdi-circle-medium' : ''">Services</v-tab>
                    <v-tab value="neuro" :prepend-icon="dirty.has('neuro_sama') ? 'mdi-circle-medium' : ''">Neuro Sama</v-tab>
                    <v-tab value="stream" :prepend-icon="dirty.has('stream') ? 'mdi-circle-medium' : ''">Stream</v-tab>
                    <v-tab value="vedal" :prepend-icon="dirty.has('vedal') ? 'mdi-circle-medium' : ''">Vedal</v-tab>
                    <v-tab value="advanced">Advanced</v-tab>
                  </v-tabs>
                </v-col>

                <v-col cols="12" sm="9" md="10">
                  <v-window v-model="tab" class="pl-sm-4">
                    <!-- ================= Services ================= -->
                    <v-window-item value="services">
                      <v-card
                        v-for="kind in (['llm', 'tts'] as const)"
                        :key="kind"
                        variant="tonal"
                        class="mb-4"
                      >
                        <v-card-title class="d-flex align-center text-body-1 pt-3">
                          <v-icon start size="small" :icon="kind === 'llm' ? 'mdi-brain' : 'mdi-volume-high'" />
                          {{ kind === 'llm' ? 'LLM Services' : 'TTS Services' }}
                          <v-spacer />
                          <v-btn size="small" variant="flat" color="primary" @click="openServiceDialog(kind, null)">
                            <v-icon start size="small">mdi-plus</v-icon>
                            添加
                          </v-btn>
                        </v-card-title>
                        <v-card-text class="pt-0">
                          <div v-if="!serviceIds(kind).length" class="text-caption text-medium-emphasis py-2">
                            {{ kind === 'llm' ? '暂无 LLM 服务，添加后可在 Neuro Sama / Chatbot 处引用。' : '暂无 TTS 服务；neuro_sama.tts 可先用内置 "null"（虚拟 TTS）。' }}
                          </div>
                          <v-list v-else lines="two" density="compact">
                            <v-list-item v-for="sid in serviceIds(kind)" :key="sid">
                              <template #prepend>
                                <v-icon :icon="kind === 'llm' ? 'mdi-open-in-app' : 'mdi-microsoft'" />
                              </template>
                              <v-list-item-title>
                                {{ serviceEntry(kind, sid).display_name || sid }}
                                <v-chip size="x-small" variant="outlined" class="ml-1 font-weight-regular">{{ sid }}</v-chip>
                              </v-list-item-title>
                              <v-list-item-subtitle>
                                <template v-if="kind === 'llm'">
                                  {{ serviceEntry(kind, sid).api_model_name || '?' }} @ {{ shortUrl(serviceEntry(kind, sid).api_base_url) }}
                                  · timeout {{ serviceEntry(kind, sid).timeout ?? 60 }}s
                                </template>
                                <template v-else>
                                  {{ serviceEntry(kind, sid).type }} · region {{ serviceEntry(kind, sid).azure_tts_region || '?' }}
                                </template>
                                <template v-if="usedBy(kind, sid).length">
                                  <span class="text-orange-darken-2"> · 引用：{{ usedBy(kind, sid).join('、') }}</span>
                                </template>
                              </v-list-item-subtitle>
                              <template #append>
                                <v-btn icon="mdi-pencil-outline" size="small" variant="text" @click="openServiceDialog(kind, sid)" />
                                <v-btn icon="mdi-delete-outline" size="small" variant="text" color="error" @click="confirmDeleteService(kind, sid)" />
                              </template>
                            </v-list-item>
                          </v-list>
                        </v-card-text>
                      </v-card>
                    </v-window-item>

                    <!-- ================= Neuro Sama ================= -->
                    <v-window-item value="neuro">
                      <v-row dense>
                        <v-col cols="12" sm="6">
                          <v-select
                            v-model="cfg.neuro_sama.model"
                            :items="llmItems"
                            label="Model（引用的 LLM 服务）"
                            variant="outlined" density="compact"
                          >
                            <template #item="{ item, props: p }">
                              <v-list-item v-bind="p" :title="item.raw.title" :subtitle="item.raw.subtitle" />
                            </template>
                          </v-select>
                        </v-col>
                        <v-col cols="12" sm="6">
                          <v-select
                            v-model="cfg.neuro_sama.tts"
                            :items="ttsItems"
                            item-title="title" item-value="value"
                            label="TTS（空=关闭 / null=虚拟 TTS / 注册服务）"
                            variant="outlined" density="compact"
                          />
                        </v-col>
                        <v-col cols="12">
                          <v-textarea
                            v-model="cfg.neuro_sama.system_prompt_base"
                            label="System prompt base"
                            variant="outlined" density="compact" rows="2" auto-grow
                          />
                        </v-col>
                        <v-col cols="6" sm="3"><v-text-field v-model.number="cfg.neuro_sama.max_steps" label="Max steps" type="number" variant="outlined" density="compact" /></v-col>
                        <v-col cols="6" sm="3"><v-text-field v-model.number="cfg.neuro_sama.max_context_messages" label="Max context messages" type="number" variant="outlined" density="compact" /></v-col>
                        <v-col cols="6" sm="3"><v-text-field v-model.number="cfg.neuro_sama.tool_timeout" label="Tool timeout (s)" type="number" step="any" variant="outlined" density="compact" /></v-col>
                        <v-col cols="6" sm="3"><v-text-field v-model.number="cfg.neuro_sama.memory_char_limit" label="Memory char limit" type="number" variant="outlined" density="compact" /></v-col>
                        <v-col cols="12" sm="4"><v-text-field v-model="cfg.neuro_sama.host" label="Host（改后需重启）" variant="outlined" density="compact" /></v-col>
                        <v-col cols="6" sm="4"><v-text-field v-model.number="cfg.neuro_sama.port" label="Port（改后需重启）" type="number" variant="outlined" density="compact" /></v-col>
                        <v-col cols="6" sm="4"><v-text-field v-model="cfg.neuro_sama.external_url" label="External URL（可选）" variant="outlined" density="compact" /></v-col>
                      </v-row>

                      <v-card variant="tonal" class="mt-2">
                        <v-card-title class="text-body-1 d-flex align-center py-2">
                          MCP Servers
                          <v-spacer />
                          <v-btn size="small" variant="flat" color="primary" @click="openMcpDialog(-1)">
                            <v-icon start size="small">mdi-plus</v-icon> 添加
                          </v-btn>
                        </v-card-title>
                        <v-card-text class="pt-0">
                          <div v-if="!cfg.neuro_sama.mcp_servers.length" class="text-caption text-medium-emphasis">未配置</div>
                          <v-list v-else density="compact" lines="one">
                            <v-list-item v-for="(m, i) in cfg.neuro_sama.mcp_servers" :key="i" :title="m.name || `#${i}`">
                              <template #prepend><v-icon :icon="'command' in m ? 'mdi-console' : 'mdi-web'" /></template>
                              <v-list-item-subtitle class="text-caption">{{ 'command' in m ? `${m.command} ${(m.args || []).join(' ')}` : m.url }}</v-list-item-subtitle>
                              <template #append>
                                <v-btn icon="mdi-pencil-outline" size="x-small" variant="text" @click="openMcpDialog(Number(i))" />
                                <v-btn icon="mdi-delete-outline" size="x-small" variant="text" color="error" @click="cfg.neuro_sama.mcp_servers.splice(i, 1)" />
                              </template>
                            </v-list-item>
                          </v-list>
                        </v-card-text>
                      </v-card>
                    </v-window-item>

                    <!-- ================= Stream ================= -->
                    <v-window-item value="stream">
                      <v-row dense>
                        <v-col cols="12" sm="4"><v-text-field v-model="cfg.stream.host" label="Host（改后需重启）" variant="outlined" density="compact" /></v-col>
                        <v-col cols="6" sm="4"><v-text-field v-model.number="cfg.stream.port" label="Port（改后需重启）" type="number" variant="outlined" density="compact" /></v-col>
                        <v-col cols="6" sm="4"><v-text-field v-model="cfg.stream.external_url" label="External URL（可选）" variant="outlined" density="compact" /></v-col>
                        <v-col cols="12" sm="6"><v-text-field v-model="cfg.stream.neuro_url" label="Neuro URL（留空 = 按 neuro_sama schema 自动）" variant="outlined" density="compact" /></v-col>
                        <v-col cols="12" sm="6"><v-text-field v-model="cfg.stream.channel" label="Channel 标识" variant="outlined" density="compact" /></v-col>
                        <v-col cols="12">
                          <v-textarea v-model="cfg.stream.scene_context" label="Scene context（每轮注入的场景描述）" variant="outlined" density="compact" rows="2" auto-grow />
                        </v-col>
                        <v-col cols="6" sm="4"><v-text-field v-model.number="cfg.stream.messages_per_round" label="Messages / round" type="number" variant="outlined" density="compact" /></v-col>
                        <v-col cols="6" sm="4"><v-text-field v-model.number="cfg.stream.round_gap_ms" label="Round gap (ms)" type="number" variant="outlined" density="compact" /></v-col>
                        <v-col cols="12" sm="4">
                          <v-select
                            v-model="cfg.stream.empty_queue_behavior"
                            :items="['placeholder', 'silent']"
                            label="空队列行为" variant="outlined" density="compact"
                          />
                        </v-col>
                        <v-col cols="12">
                          <v-textarea v-model="cfg.stream.placeholder_message" label="Placeholder message" variant="outlined" density="compact" rows="1" auto-grow />
                        </v-col>
                      </v-row>

                      <v-card variant="tonal" class="mt-2">
                        <v-card-title class="text-body-1 py-2 d-flex align-center">
                          Chatbot（模拟观众弹幕）
                          <v-spacer />
                          <v-switch v-model="cfg.stream.chatbot.enabled" hide-details density="compact" inset color="primary" class="mt-0" />
                        </v-card-title>
                        <v-card-text class="pt-0">
                          <v-row dense>
                            <v-col cols="12" sm="6">
                              <v-select
                                v-model="cfg.stream.chatbot.model"
                                :items="llmItems"
                                label="Model（引用的 LLM 服务）"
                                variant="outlined" density="compact"
                              >
                                <template #item="{ item, props: p }">
                                  <v-list-item v-bind="p" :title="item.raw.title" :subtitle="item.raw.subtitle" />
                                </template>
                              </v-select>
                            </v-col>
                            <v-col cols="6" sm="2"><v-text-field v-model.number="cfg.stream.chatbot.interval_s" label="Interval (s)" type="number" step="any" variant="outlined" density="compact" /></v-col>
                            <v-col cols="6" sm="2"><v-text-field v-model.number="cfg.stream.chatbot.count" label="每次条数" type="number" variant="outlined" density="compact" /></v-col>
                            <v-col cols="6" sm="2"><v-text-field v-model.number="cfg.stream.chatbot.neuro_sleep_timeout_s" label="Sleep 判定 (s)" type="number" step="any" variant="outlined" density="compact" /></v-col>
                            <v-col cols="12">
                              <v-textarea
                                v-model="cfg.stream.chatbot.system_prompt"
                                label="System prompt（{count} 为占位符；留空用内置默认）"
                                variant="outlined" density="compact" rows="2" auto-grow
                              />
                            </v-col>
                          </v-row>
                        </v-card-text>
                      </v-card>
                    </v-window-item>

                    <!-- ================= Vedal ================= -->
                    <v-window-item value="vedal">
                      <v-row dense>
                        <v-col cols="12" sm="5"><v-text-field v-model="cfg.vedal.host" label="Host（改后需重启 vedal）" variant="outlined" density="compact" /></v-col>
                        <v-col cols="6" sm="3"><v-text-field v-model.number="cfg.vedal.port" label="Port（改后需重启 vedal）" type="number" variant="outlined" density="compact" /></v-col>
                        <v-col cols="6" sm="4"><v-text-field v-model="cfg.vedal.external_url" label="External URL（可选）" variant="outlined" density="compact" /></v-col>
                      </v-row>
                      <div class="text-caption text-medium-emphasis mt-2">
                        vedal 是 dashboard 与 OBS 的唯一入口（模块代理 / 静态托管均经此转发）。
                      </div>
                    </v-window-item>

                    <!-- ================= Advanced ================= -->
                    <v-window-item value="advanced">
                      <div v-if="!extraSchemas.length" class="text-caption text-medium-emphasis py-2">
                        全部配置已由上方表单接管（Services = server schema）。未来出现的新模块 schema 会在此处提供 JSON 编辑器。
                      </div>
                      <div v-for="name in extraSchemas" :key="name" class="mb-4">
                        <div class="text-subtitle-2 mb-1">{{ name }}</div>
                        <v-textarea
                          :model-value="extraRaw[name]"
                          variant="outlined" density="compact" auto-grow rows="8" class="font-mono"
                          :error-messages="extraErrors[name] || []"
                          @update:model-value="(v: any) => onExtraInput(name, String(v ?? ''))"
                        />
                      </div>
                    </v-window-item>
                  </v-window>
                </v-col>
              </v-row>
            </template>

            <v-alert v-else-if="!busy" type="warning" variant="tonal">配置未加载。点击"重新读取"。</v-alert>
          </v-card-text>

          <v-divider />
          <div class="d-flex align-center pa-3">
            <span v-if="dirtyCount" class="text-caption text-warning text-medium-emphasis">
              有 {{ dirtyCount }} 个部分未保存
            </span>
            <v-spacer />
            <v-btn color="success" :disabled="!dirtyCount || busy" @click="save">
              <v-icon left>mdi-content-save</v-icon>
              保存并重载
            </v-btn>
            <v-btn color="warning" variant="text" class="ml-2" :disabled="busy" @click="discard">
              <v-icon left>mdi-undo</v-icon>
              放弃更改
            </v-btn>
            <v-btn variant="text" class="ml-2" :disabled="busy" @click="reloadAll">
              <v-icon left>mdi-restart</v-icon>
              重载所有
            </v-btn>
          </div>
        </v-card>
      </v-col>
    </v-row>

    <!-- ============ 服务编辑对话框 ============ -->
    <v-dialog v-model="svcDialog" max-width="560" persistent>
      <v-card v-if="svcDraft">
        <v-card-title>{{ svcEditing ? '编辑' : '添加' }} {{ svcKind === 'llm' ? 'LLM' : 'TTS' }} 服务</v-card-title>
        <v-card-text>
          <v-row dense>
            <v-col cols="12" sm="6">
              <v-text-field
                v-model="svcDraft.id" label="Service ID（引用键）" variant="outlined" density="compact"
                :disabled="!!svcEditing" :rules="[idRule]"
              />
            </v-col>
            <v-col cols="12" sm="6"><v-text-field v-model="svcDraft.display_name" label="Display name" variant="outlined" density="compact" /></v-col>
            <template v-if="svcKind === 'llm'">
              <v-col cols="12"><v-text-field v-model="svcDraft.api_base_url" label="API base URL（如 https://api.example.com/v1）" variant="outlined" density="compact" /></v-col>
              <v-col cols="12" sm="7">
                <v-text-field
                  v-model="svcDraft.api_key" label="API key" variant="outlined" density="compact"
                  :type="svcDraft.showKey ? 'text' : 'password'" :append-icon="svcDraft.showKey ? 'mdi-eye-off' : 'mdi-eye'"
                  @click:append="svcDraft.showKey = !svcDraft.showKey"
                />
              </v-col>
              <v-col cols="12" sm="5"><v-text-field v-model="svcDraft.api_model_name" label="API model name" variant="outlined" density="compact" /></v-col>
              <v-col cols="12">
                <v-textarea
                  v-model="svcDraft.extraRaw" label="Extra body（JSON，随请求体下发；如 {&quot;thinking&quot;: {&quot;type&quot;: &quot;disabled&quot;}}）"
                  variant="outlined" density="compact" rows="3" auto-grow class="font-mono"
                  :error-messages="svcDraft.extraError ? [svcDraft.extraError] : []"
                />
              </v-col>
              <v-col cols="12" sm="4"><v-text-field v-model.number="svcDraft.timeout" label="Timeout (s)" type="number" step="any" variant="outlined" density="compact" /></v-col>
            </template>
            <template v-else>
              <v-col cols="12" sm="6">
                <v-select v-model="svcDraft.type" :items="['azure_tts']" label="Type" variant="outlined" density="compact" />
              </v-col>
              <v-col cols="12" sm="6"><v-text-field v-model="svcDraft.azure_tts_region" label="Azure region（如 eastus）" variant="outlined" density="compact" /></v-col>
              <v-col cols="12">
                <v-text-field
                  v-model="svcDraft.azure_tts_key" label="Azure subscription key" variant="outlined" density="compact"
                  :type="svcDraft.showKey ? 'text' : 'password'" :append-icon="svcDraft.showKey ? 'mdi-eye-off' : 'mdi-eye'"
                  @click:append="svcDraft.showKey = !svcDraft.showKey"
                />
              </v-col>
              <v-col cols="12" sm="4"><v-text-field v-model.number="svcDraft.timeout" label="Timeout (s)" type="number" step="any" variant="outlined" density="compact" /></v-col>
            </template>
          </v-row>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="svcDialog = false">取消</v-btn>
          <v-btn color="primary" variant="flat" @click="commitService">确定</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- ============ 删除服务确认（含引用警告） ============ -->
    <v-dialog v-model="delDialog" max-width="460">
      <v-card v-if="delTarget">
        <v-card-title class="text-h6">删除服务 {{ delTarget.id }}？</v-card-title>
        <v-card-text>
          <template v-if="delTarget.refs.length">
            <v-alert type="warning" variant="tonal" density="compact" class="mb-2">
              该服务正被以下配置引用，删除后这些引用将被自动清空：
              <ul class="mt-1 mb-0">
                <li v-for="r in delTarget.refs" :key="r">{{ r }}</li>
              </ul>
            </v-alert>
          </template>
          <div v-else class="text-body-2">该服务当前没有被引用。</div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="delDialog = false">取消</v-btn>
          <v-btn color="error" variant="flat" @click="doDeleteService">删除</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- ============ MCP 编辑对话框 ============ -->
    <v-dialog v-model="mcpDialog" max-width="560" persistent>
      <v-card v-if="mcpDraft">
        <v-card-title>{{ mcpEditing >= 0 ? '编辑' : '添加' }} MCP Server</v-card-title>
        <v-card-text>
          <v-row dense>
            <v-col cols="12" sm="6"><v-text-field v-model="mcpDraft.name" label="Name（工具前缀）" variant="outlined" density="compact" /></v-col>
            <v-col cols="12" sm="6">
              <v-select v-model="mcpDraft.transport" :items="[{ title: 'stdio（本地命令）', value: 'stdio' }, { title: 'streamable HTTP', value: 'http' }]" label="Transport" variant="outlined" density="compact" />
            </v-col>
            <template v-if="mcpDraft.transport === 'stdio'">
              <v-col cols="12"><v-text-field v-model="mcpDraft.command" label="Command（如 npx）" variant="outlined" density="compact" /></v-col>
              <v-col cols="12">
                <v-textarea v-model="mcpDraft.argsRaw" label="Args（每行一个参数）" variant="outlined" density="compact" rows="2" auto-grow class="font-mono" />
              </v-col>
              <v-col cols="12">
                <v-textarea v-model="mcpDraft.envRaw" label="Env（每行 KEY=VALUE，可空）" variant="outlined" density="compact" rows="2" auto-grow class="font-mono" />
              </v-col>
            </template>
            <template v-else>
              <v-col cols="12"><v-text-field v-model="mcpDraft.url" label="URL（如 http://127.0.0.1:9000/mcp）" variant="outlined" density="compact" /></v-col>
            </template>
          </v-row>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="mcpDialog = false">取消</v-btn>
          <v-btn color="primary" variant="flat" @click="commitMcp">确定</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-container>
</template>

<script lang="ts" setup>
import { ref, computed, onMounted } from 'vue'
import { api, ApiError } from '@/stores/connection'

// ---------- 类型 ----------
interface LLMEntry {
  id: string
  display_name: string
  api_base_url: string
  api_key: string
  api_model_name: string
  extraRaw: string
  extraError: string
  timeout: number
  showKey?: boolean
}
interface TTSEntry {
  id: string
  display_name: string
  type: string
  azure_tts_region: string
  azure_tts_key: string
  timeout: number
  showKey?: boolean
}
type ServiceKind = 'llm' | 'tts'

interface SaveResult {
  status: string
  changed_schemas: string[]
  reloads: Record<string, { status: string; detail?: string }>
  cleared_references: string[]
}

const KNOWN_SCHEMAS = ['server', 'neuro_sama', 'stream', 'vedal']

// ---------- 状态 ----------
const cfg = ref<Record<string, any> | null>(null)
const baseline = ref('')
const tab = ref('services')
const busy = ref(false)
const msg = ref('')
const msgType = ref<'success' | 'error' | 'warning' | 'info'>('info')

const svcDialog = ref(false)
const svcKind = ref<ServiceKind>('llm')
const svcEditing = ref<string | null>(null)
const svcDraft = ref<LLMEntry & TTSEntry | null>(null)

const delDialog = ref(false)
const delTarget = ref<{ kind: ServiceKind; id: string; refs: string[] } | null>(null)

const mcpDialog = ref(false)
const mcpEditing = ref(-1)
const mcpDraft = ref<any>(null)

const extraRaw = ref<Record<string, string>>({})
const extraErrors = ref<Record<string, string>>({})

const idRule = (v: string) =>
  /^[A-Za-z0-9_-]{1,64}$/.test(v || '') || '仅允许字母/数字/-/_（1-64 位）'

// ---------- 加载 ----------
const normalize = (doc: Record<string, any>) => {
  doc.server = doc.server || {}
  doc.server.llm_services = doc.server.llm_services || {}
  doc.server.tts_services = doc.server.tts_services || {}
  doc.neuro_sama = doc.neuro_sama || {}
  const n = doc.neuro_sama
  n.model = n.model ?? ''
  n.tts = n.tts ?? 'null'
  n.system_prompt_base = n.system_prompt_base ?? 'You are Neuro-Sama, an AI Vtuber streaming on Twitch.'
  n.max_steps = n.max_steps ?? 10
  n.max_context_messages = n.max_context_messages ?? 60
  n.tool_timeout = n.tool_timeout ?? 30
  n.memory_char_limit = n.memory_char_limit ?? 4000
  n.mcp_servers = n.mcp_servers || []
  n.host = n.host ?? '127.0.0.1'
  n.port = n.port ?? 8000
  n.external_url = n.external_url ?? ''
  doc.stream = doc.stream || {}
  const s = doc.stream
  s.host = s.host ?? '127.0.0.1'
  s.port = s.port ?? 8200
  s.external_url = s.external_url ?? ''
  s.neuro_url = s.neuro_url ?? ''
  s.channel = s.channel ?? 'stream'
  s.scene_context = s.scene_context ?? 'You are streaming on Twitch now.'
  s.messages_per_round = s.messages_per_round ?? 10
  s.round_gap_ms = s.round_gap_ms ?? 1500
  s.empty_queue_behavior = s.empty_queue_behavior ?? 'placeholder'
  s.placeholder_message = s.placeholder_message ?? 'No new messages. Say something to your viewers!'
  s.chatbot = s.chatbot || {}
  const c = s.chatbot
  c.enabled = c.enabled ?? true
  c.model = c.model ?? ''
  c.system_prompt = c.system_prompt ?? ''
  c.interval_s = c.interval_s ?? 10
  c.count = c.count ?? 3
  c.neuro_sleep_timeout_s = c.neuro_sleep_timeout_s ?? 45
  doc.vedal = doc.vedal || {}
  doc.vedal.host = doc.vedal.host ?? '127.0.0.1'
  doc.vedal.port = doc.vedal.port ?? 8100
  doc.vedal.external_url = doc.vedal.external_url ?? ''
}

const loadConfig = async () => {
  try {
    busy.value = true
    const data = await api<{ config: Record<string, any> }>('/manage/config')
    normalize(data.config)
    cfg.value = data.config
    baseline.value = JSON.stringify(data.config)
    resetExtra()
    msg.value = ''
  } catch (e) {
    msg.value = '加载配置失败: ' + errText(e)
    msgType.value = 'error'
  } finally {
    busy.value = false
  }
}

const errText = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : 'Unknown error')

const resetExtra = () => {
  extraRaw.value = {}
  extraErrors.value = {}
  for (const name of extraSchemas.value) {
    extraRaw.value[name] = JSON.stringify(cfg.value![name], null, 2)
  }
}

const extraSchemas = computed(() =>
  cfg.value ? Object.keys(cfg.value).filter((k) => !KNOWN_SCHEMAS.includes(k)) : []
)

const onExtraInput = (name: string, v: string) => {
  extraRaw.value[name] = v
  if (!cfg.value) return
  try {
    const parsed = JSON.parse(v || '{}')
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      throw new Error('必须是 JSON 对象')
    }
    cfg.value[name] = parsed
    extraErrors.value[name] = ''
  } catch (e) {
    extraErrors.value[name] = 'JSON 不合法: ' + (e instanceof Error ? e.message : String(e))
  }
}

// ---------- 脏检测（逐顶层 schema 与基线深比较） ----------
const dirty = computed(() => {
  const set = new Set<string>()
  if (!cfg.value) return set
  const baseObj = JSON.parse(baseline.value || '{}')
  const names = new Set<string>([...Object.keys(baseObj), ...Object.keys(cfg.value)])
  for (const name of names) {
    if (extraErrors.value[name]) {
      set.add(name)
      continue
    }
    const now = JSON.stringify(cfg.value[name] ?? null)
    const was = JSON.stringify(baseObj[name] ?? null)
    if (now !== was) set.add(name)
  }
  return set
})
const dirtyCount = computed(() => dirty.value.size)

// ---------- 服务注册表 ----------
const serviceIds = (kind: ServiceKind): string[] =>
  cfg.value ? Object.keys(kind === 'llm' ? cfg.value.server.llm_services : cfg.value.server.tts_services) : []

const serviceEntry = (kind: ServiceKind, id: string) =>
  (kind === 'llm' ? cfg.value?.server.llm_services[id] : cfg.value?.server.tts_services[id]) || {}

/** 某服务被哪些（人类可读的）配置项引用——基于当前工作副本实时计算 */
const usedBy = (kind: ServiceKind, id: string): string[] => {
  if (!cfg.value) return []
  const refs: string[] = []
  if (kind === 'llm') {
    if (cfg.value.neuro_sama.model === id) refs.push('Neuro Sama · Model')
    if (cfg.value.stream.chatbot.model === id) refs.push('Stream · Chatbot Model')
  } else {
    if (cfg.value.neuro_sama.tts === id) refs.push('Neuro Sama · TTS')
  }
  return refs
}

const llmItems = computed(() => [
  { title: '— 未配置 —', value: '', subtitle: 'neuro 对话将明确报错 / chatbot 暂停' },
  ...serviceIds('llm').map((id) => ({
    title: cfg.value!.server.llm_services[id].display_name || id,
    value: id,
      subtitle: `${cfg.value!.server.llm_services[id].api_model_name || '?'} · ${shortUrl(cfg.value!.server.llm_services[id].api_base_url)}`,
    })),
])

const ttsItems = computed(() => [
  { title: '— 关闭（不产语音事件）', value: '' },
  { title: 'null（内置虚拟 TTS：无音频、估算时长驱动字幕）', value: 'null' },
  ...serviceIds('tts').map((id) => ({
    title: cfg.value!.server.tts_services[id].display_name || id,
    value: id,
  })),
])

const shortUrl = (u?: string) => {
  if (!u) return '?'
  try {
    return new URL(u).host
  } catch {
    return u
  }
}

const openServiceDialog = (kind: ServiceKind, id: string | null) => {
  svcKind.value = kind
  svcEditing.value = id
  const e = id ? (kind === 'llm' ? cfg.value!.server.llm_services[id] : cfg.value!.server.tts_services[id]) : {}
  svcDraft.value = {
    id: id || '',
    display_name: e.display_name || '',
    api_base_url: e.api_base_url || '',
    api_key: e.api_key || '',
    api_model_name: e.api_model_name || '',
    extraRaw: e.api_extra_body ? JSON.stringify(e.api_extra_body, null, 2) : '',
    extraError: '',
    timeout: e.timeout ?? (kind === 'llm' ? 60 : 10),
    type: e.type || 'azure_tts',
    azure_tts_region: e.azure_tts_region || '',
    azure_tts_key: e.azure_tts_key || '',
    showKey: false,
  }
  svcDialog.value = true
}

const commitService = () => {
  const d = svcDraft.value
  if (!d || !cfg.value) return
  if (!idRule(d.id)) {
    msg.value = 'Service ID 不合法：' + idRule(d.id)
    msgType.value = 'error'
    return
  }
  const reg = svcKind.value === 'llm' ? cfg.value.server.llm_services : cfg.value.server.tts_services
  if (!svcEditing.value && reg[d.id]) {
    msg.value = `服务 id ${d.id} 已存在`
    msgType.value = 'error'
    return
  }
  let entry: Record<string, any>
  if (svcKind.value === 'llm') {
    let extra: Record<string, any> = {}
    if (d.extraRaw && d.extraRaw.trim()) {
      try {
        extra = JSON.parse(d.extraRaw)
        if (typeof extra !== 'object' || extra === null || Array.isArray(extra)) throw new Error('必须是 JSON 对象')
      } catch (e) {
        d.extraError = 'JSON 不合法'
        return
      }
    }
    entry = {
      display_name: d.display_name || d.id,
      api_base_url: d.api_base_url,
      api_key: d.api_key,
      api_model_name: d.api_model_name,
      api_extra_body: extra,
      timeout: Number(d.timeout) || 60,
    }
  } else {
    entry = {
      display_name: d.display_name || d.id,
      type: d.type,
      azure_tts_region: d.azure_tts_region,
      azure_tts_key: d.azure_tts_key,
      timeout: Number(d.timeout) || 10,
    }
  }
  if (svcEditing.value && svcEditing.value !== d.id) {
    delete reg[svcEditing.value]
    // 改 id 视同删除+新建：同步改写引用（本会话内安全，服务端还会做悬空兜底清理）
    rewriteRefs(svcKind.value, svcEditing.value, d.id)
  }
  reg[d.id] = entry
  svcDialog.value = false
}

const rewriteRefs = (kind: ServiceKind, from: string, to: string) => {
  if (!cfg.value) return
  if (kind === 'llm') {
    if (cfg.value.neuro_sama.model === from) cfg.value.neuro_sama.model = to
    if (cfg.value.stream.chatbot.model === from) cfg.value.stream.chatbot.model = to
  } else if (cfg.value.neuro_sama.tts === from) {
    cfg.value.neuro_sama.tts = to
  }
}

const confirmDeleteService = (kind: ServiceKind, id: string) => {
  delTarget.value = { kind, id, refs: usedBy(kind, id) }
  delDialog.value = true
}

const doDeleteService = () => {
  const t = delTarget.value
  if (!t || !cfg.value) return
  const reg = t.kind === 'llm' ? cfg.value.server.llm_services : cfg.value.server.tts_services
  delete reg[t.id]
  if (t.refs.length) {
    if (t.kind === 'llm') {
      if (cfg.value.neuro_sama.model === t.id) cfg.value.neuro_sama.model = ''
      if (cfg.value.stream.chatbot.model === t.id) cfg.value.stream.chatbot.model = ''
    } else if (cfg.value.neuro_sama.tts === t.id) {
      cfg.value.neuro_sama.tts = 'null'
    }
    msg.value = `已删除服务 ${t.id} 并清空其引用（保存后生效）`
    msgType.value = 'warning'
  }
  delDialog.value = false
}

// ---------- MCP ----------
const openMcpDialog = (i: number) => {
  mcpEditing.value = i
  const e = i >= 0 ? cfg.value!.neuro_sama.mcp_servers[i] : null
  mcpDraft.value = {
    name: e?.name || '',
    transport: e && 'command' in e ? 'stdio' : 'http',
    command: e?.command || '',
    argsRaw: (e?.args || []).join('\n'),
    envRaw: Object.entries(e?.env || {}).map(([k, v]) => `${k}=${v}`).join('\n'),
    url: e?.url || '',
  }
  mcpDialog.value = true
}

const commitMcp = () => {
  const d = mcpDraft.value
  if (!d || !cfg.value) return
  const list = cfg.value.neuro_sama.mcp_servers
  const base: Record<string, any> = { name: d.name || 'mcp' }
  if (d.transport === 'stdio') {
    base.command = d.command
    const args = (d.argsRaw || '').split('\n').map((x: string) => x.trim()).filter(Boolean)
    if (args.length) base.args = args
    const env: Record<string, string> = {}
    for (const line of (d.envRaw || '').split('\n').map((x: string) => x.trim()).filter(Boolean)) {
      const i = line.indexOf('=')
      if (i > 0) env[line.slice(0, i)] = line.slice(i + 1)
    }
    if (Object.keys(env).length) base.env = env
  } else {
    base.url = d.url
  }
  if (mcpEditing.value >= 0) list.splice(mcpEditing.value, 1, base)
  else list.push(base)
  mcpDialog.value = false
}

// ---------- 保存 / 放弃 / 重载 ----------
const save = async () => {
  if (!cfg.value) return
  if (Object.values(extraErrors.value).some(Boolean)) {
    msg.value = 'Advanced 页存在 JSON 语法错误，已阻止保存'
    msgType.value = 'error'
    return
  }
  try {
    busy.value = true
    const data = await api<SaveResult>('/manage/config', {
      method: 'PUT',
      body: JSON.stringify({ config: cfg.value }),
    })
    const summary = !data.changed_schemas.length
      ? '已保存（服务端对比无实际变动，未触发重载）'
      : (() => {
          const reloadInfo = Object.entries(data.reloads)
            .map(([m, r]) => `${m}=${r.status}${r.detail ? ` (${r.detail})` : ''}`)
            .join(', ')
          const cleared = data.cleared_references?.length
            ? `；服务端兜底清空引用: ${data.cleared_references.join(', ')}`
            : ''
          const failed = Object.values(data.reloads).some((r) => r.status !== 'ok')
          msgType.value = failed ? 'warning' : 'success'
          return `已保存 [${data.changed_schemas.join(', ')}]；重载: ${reloadInfo || '-'}${cleared}`
        })()
    await loadConfig()
    msg.value = summary
  } catch (e) {
    msg.value = '保存失败: ' + errText(e)
    msgType.value = 'error'
  } finally {
    busy.value = false
  }
}

const discard = async () => {
  if (dirtyCount.value && !confirm('放弃所有未保存的更改？')) return
  await loadConfig()
  msg.value = '已重置为服务器当前配置'
  msgType.value = 'info'
}

const reloadAll = async () => {
  try {
    busy.value = true
    const data = await api<SaveResult>('/manage/config/reload-all', { method: 'POST' })
    const reloadInfo = Object.entries(data.reloads)
      .map(([m, r]) => `${m}=${r.status}${r.detail ? ` (${r.detail})` : ''}`)
      .join(', ')
    msg.value = `重载完成: ${reloadInfo || 'none'}`
    msgType.value = 'success'
  } catch (e) {
    msg.value = '重载失败: ' + errText(e)
    msgType.value = 'error'
  } finally {
    busy.value = false
  }
}

onMounted(loadConfig)
</script>

<style scoped>
.font-mono {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
}
</style>
