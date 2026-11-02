<template>
  <div class="text-caption text-medium-emphasis mb-2">
    坐标相对画面（舞台）尺寸，100 = 满屏；负值可把元素移出画面。
  </div>
  <div v-if="crossScene" class="text-caption text-medium-emphasis mb-2">
    将同时应用到 neuro 的全部场景（visible 按场景单独保存）。
  </div>

  <div class="text-subtitle-2 mb-1">定位</div>
  <v-row dense>
    <v-col cols="12" sm="6">
      <v-select
        v-model="form.placement"
        :items="PLACEMENTS"
        item-title="title"
        item-value="value"
        label="定位方式"
        variant="outlined"
        density="compact"
        @update:model-value="emit('change')"
      />
    </v-col>
    <v-col cols="12" sm="6">
      <v-select
        :items="['舞台（默认）']"
        model-value="舞台（默认）"
        label="参照"
        variant="outlined"
        density="compact"
        disabled
        hint="元素参照：即将推出"
        persistent-hint
      />
    </v-col>
    <v-col cols="6" sm="3">
      <v-text-field
        v-model="form.x"
        :label="xFieldLabel"
        type="number"
        variant="outlined"
        density="compact"
        hide-details
        @update:model-value="emit('change')"
      />
    </v-col>
    <v-col cols="6" sm="3">
      <v-text-field
        v-model="form.y"
        :label="yFieldLabel"
        type="number"
        variant="outlined"
        density="compact"
        hide-details
        @update:model-value="emit('change')"
      />
    </v-col>
    <v-col cols="6" sm="3">
      <v-text-field
        v-model="form.w"
        label="宽度（%画面宽）"
        type="number"
        variant="outlined"
        density="compact"
        hide-details
        @update:model-value="emit('change')"
      />
    </v-col>
    <v-col cols="6" sm="3">
      <v-text-field
        v-model="form.h"
        label="高度（%画面高，空=自动）"
        type="number"
        variant="outlined"
        density="compact"
        hide-details
        @update:model-value="emit('change')"
      />
    </v-col>
  </v-row>

  <v-divider class="my-3" />
  <div class="text-subtitle-2 mb-1">外观</div>
  <v-row dense>
    <v-col cols="4">
      <v-text-field
        v-model="form.scale"
        label="scale（空=1）"
        type="number"
        variant="outlined"
        density="compact"
        hide-details
        @update:model-value="emit('change')"
      />
    </v-col>
    <v-col cols="4">
      <v-text-field
        v-model="form.rotate"
        label="rotate（空=0）"
        type="number"
        variant="outlined"
        density="compact"
        hide-details
        @update:model-value="emit('change')"
      />
    </v-col>
    <v-col cols="4">
      <v-text-field
        v-model="form.opacity"
        label="opacity（空=1）"
        type="number"
        variant="outlined"
        density="compact"
        hide-details
        @update:model-value="emit('change')"
      />
    </v-col>
  </v-row>

  <template v-if="showContent">
    <v-divider class="my-3" />
    <div class="text-subtitle-2 mb-1">缩放</div>
    <v-select
      v-model="form.resize"
      :items="resizeItems"
      label="缩放约束（空 = 组件默认）"
      variant="outlined"
      density="compact"
      class="mb-1"
      @update:model-value="emit('change')"
    />
    <div class="text-caption text-disabled mb-2">
      当前默认：{{ resizeDefaultLabel }}。ratio 只给角手柄（等比），width 只给左右边，none 只能移动。
    </div>
  </template>

  <v-divider class="my-3" />
  <div class="text-subtitle-2 mb-1">层级</div>
  <v-row dense align="center">
    <v-col cols="6">
      <v-text-field
        v-model="form.z"
        label="层级（越大越靠前）"
        type="number"
        variant="outlined"
        density="compact"
        hide-details
        @update:model-value="emit('change')"
      />
    </v-col>
    <v-col cols="6">
      <v-switch
        v-model="form.visible"
        label="visible（数据层声明）"
        color="primary"
        density="compact"
        hide-details
        @update:model-value="emit('change')"
      />
    </v-col>
  </v-row>
  <div class="text-caption text-disabled">
    visible 是数据层声明；眼睛按钮是运行时强制隐藏通道，二者互不改写。
  </div>

  <!-- 内容归各清单自己管：neuro 内部层不开放内容区（组件内容属于 neuro 模板） -->
  <template v-if="showContent">
    <v-divider class="my-3" />
    <div class="text-subtitle-2 mb-1">内容</div>
    <div class="d-flex align-center ga-2 mb-2">
      <span class="text-body-2">类型</span>
      <v-chip size="small" variant="tonal">{{ kindLabel }}</v-chip>
      <span class="text-caption text-disabled">类型由创建时决定，不可改</span>
    </div>
    <template v-if="form.kind === 'color'">
      <v-text-field
        v-model="form.color"
        label="颜色值（color）"
        placeholder="#352634"
        variant="outlined"
        density="compact"
        @update:model-value="emit('change')"
      />
    </template>
    <template v-else-if="form.kind === 'image' || form.kind === 'video'">
      <v-text-field
        v-model="form.src"
        label="素材地址（src；裸名 = 后端资产）"
        variant="outlined"
        density="compact"
        @update:model-value="emit('change')"
      />
      <div class="d-flex align-center ga-2 mb-2">
        <v-btn
          size="small"
          variant="tonal"
          prepend-icon="mdi-upload"
          :loading="uploading"
          :disabled="uploading"
          @click="fileInput?.click()"
        >
          上传
        </v-btn>
        <v-btn
          size="small"
          variant="tonal"
          prepend-icon="mdi-link-variant-plus"
          :loading="uploading"
          :disabled="uploading"
          @click="importFromUrl"
        >
          从链接导入
        </v-btn>
        <input
          ref="fileInput"
          type="file"
          class="d-none"
          accept=".webp,.png,.jpg,.jpeg,.gif,.svg,.mp4,.webm"
          @change="onUploadFile"
        />
      </div>
      <v-select
        :items="assetItems"
        :loading="assetLoading"
        label="从已有资产选择"
        variant="outlined"
        density="compact"
        clearable
        hide-details
        class="mb-2"
        @update:model-value="onPickAsset"
      />
      <template v-if="form.kind === 'video'">
        <v-row dense align="center">
          <v-col cols="6">
            <v-select
              v-model="form.videoLoop"
              :items="[
                { title: '单次播放', value: false },
                { title: '循环播放', value: true },
              ]"
              item-title="title"
              item-value="value"
              label="播放模式"
              variant="outlined"
              density="compact"
              hide-details
              @update:model-value="emit('change')"
            />
          </v-col>
          <v-col cols="6">
            <v-switch
              v-model="form.videoMuted"
              label="静音（muted，默认开）"
              color="primary"
              density="compact"
              hide-details
              @update:model-value="emit('change')"
            />
          </v-col>
        </v-row>
      </template>
    </template>
    <template v-else-if="form.kind === 'text'">
      <v-text-field
        v-model="form.text"
        label="文本（text）"
        variant="outlined"
        density="compact"
        @update:model-value="emit('change')"
      />
      <v-row dense>
        <v-col cols="6" sm="3">
          <v-select
            v-model="form.fontFamily"
            :items="['', 'Inter', 'Noto Sans SC', 'First Coffee', 'Causten']"
            label="字体（空=默认）"
            variant="outlined"
            density="compact"
            hide-details
            @update:model-value="emit('change')"
          />
        </v-col>
        <v-col cols="6" sm="3">
          <v-text-field
            v-model="form.fontSize"
            label="字号（舞台宽比例，空=0.03）"
            type="number"
            step="0.001"
            variant="outlined"
            density="compact"
            hide-details
            @update:model-value="emit('change')"
          />
        </v-col>
        <v-col cols="6" sm="2">
          <v-text-field
            v-model="form.fontColor"
            label="颜色"
            placeholder="#ffffff"
            variant="outlined"
            density="compact"
            hide-details
            @update:model-value="emit('change')"
          />
        </v-col>
        <v-col cols="6" sm="2">
          <v-select
            v-model="form.fontWeight"
            :items="['', '300', '400', '600', '800']"
            label="字重"
            variant="outlined"
            density="compact"
            hide-details
            @update:model-value="emit('change')"
          />
        </v-col>
        <v-col cols="6" sm="2">
          <v-select
            v-model="form.fontAlign"
            :items="[
              { title: '对齐（默认左）', value: '' },
              { title: '左', value: 'left' },
              { title: '中', value: 'center' },
              { title: '右', value: 'right' },
            ]"
            item-title="title"
            item-value="value"
            label="对齐"
            variant="outlined"
            density="compact"
            hide-details
            @update:model-value="emit('change')"
          />
        </v-col>
      </v-row>
      <div class="text-caption text-disabled">
        字号相对**舞台宽度**：改元素尺寸不改字号（与 chat 同机制）。
      </div>
    </template>
    <template v-else-if="form.kind === 'iframe'">
      <v-text-field
        v-model="form.src"
        label="网页地址（src：上传的 html 资产名或 http(s) 外链）"
        variant="outlined"
        density="compact"
        class="mb-2"
        @update:model-value="emit('change')"
      />
      <v-row dense>
        <v-col cols="6">
          <v-text-field
            v-model="form.sandbox"
            label="sandbox（空 = allow-scripts）"
            variant="outlined"
            density="compact"
            hide-details
            @update:model-value="emit('change')"
          />
        </v-col>
        <v-col cols="6">
          <v-text-field
            v-model="form.title"
            label="title（无障碍描述，可空）"
            variant="outlined"
            density="compact"
            hide-details
            @update:model-value="emit('change')"
          />
        </v-col>
      </v-row>
      <div class="text-caption text-disabled mt-1">
        外链 = OBS 浏览器源语义（观看者浏览器直连，X-Frame-Options 拒绝时显示空白）；
        sandbox 禁止 allow-same-origin（保存会被后端 400）。
      </div>
    </template>
    <template v-else-if="form.kind === 'component'">
      <v-text-field
        :model-value="form.name"
        label="组件名（name，锁定）"
        variant="outlined"
        density="compact"
        class="mb-2"
        disabled
        title="组件由代码管理，删除重建请用重置"
      />
      <v-textarea
        v-model="form.props"
        label="props JSON（空 = 不带该键）"
        :error-messages="propsError ? [propsError] : []"
        variant="outlined"
        density="compact"
        rows="3"
        spellcheck="false"
        class="studio-json"
        @update:model-value="emit('change')"
      />
    </template>
  </template>

  <v-divider class="my-3" />
  <div class="text-subtitle-2 mb-1">脚本</div>
  <v-textarea
    v-model="form.script"
    label="script JSON（enter / exit / tween；空 = 删键）"
    :error-messages="scriptError ? [scriptError] : []"
    variant="outlined"
    density="compact"
    rows="5"
    spellcheck="false"
    class="studio-json"
    @update:model-value="emit('change')"
  />

  <v-text-field
    v-if="lockedId"
    class="mt-3"
    :model-value="lockedId"
    label="元素 id（锁定）"
    variant="outlined"
    density="compact"
    disabled
    title="改名请删除重建或重置"
  />
</template>

<script setup lang="ts">
/**
 * 元素几何/外观/层级/内容/脚本表单——Studio 元素对话框与 neuro 窗口内部层面板共用。
 *
 * `form` 是调用方的 reactive 单例（同一时刻只有一个表单在活动：元素对话框或
 * neuro 面板手风琴展开的那一层）。子组件直接改它的属性、通过 change 事件让
 * 调用方执行"表单 → 清单 → 防抖保存"的转换——字段语义（百分比换算、edge
 * 映射、JSON 解析）全部留在调用方，这里只是控件布局。
 */
import { computed, onMounted, ref, watch } from 'vue'

import type { ResizeConstraint } from '@/ui/stream/resize'
import { COMPONENT_RESIZE } from '@/ui/stream/resize'

export interface ElementFormState {
  placement: string
  x: string
  y: string
  w: string
  h: string
  scale: string
  rotate: string
  opacity: string
  z: string
  visible: boolean
  kind: string
  color: string
  src: string
  text: string
  name: string
  props: string
  script: string
  resize: string
  sandbox: string
  title: string
  videoMuted: boolean
  videoLoop: boolean
  fontFamily: string
  fontSize: string
  fontWeight: string
  fontColor: string
  fontAlign: string
}

const props = withDefaults(defineProps<{
  form: ElementFormState
  scriptError?: string
  propsError?: string
  /** neuro 内部层不开放内容区（内容归 neuro 模板管）。 */
  showContent?: boolean
  /** 非空时显示锁定的元素 id 字段。 */
  lockedId?: string
  /** neuro 层：提示改动会应用到全部 neuro 场景。 */
  crossScene?: boolean
}>(), {
  scriptError: '',
  propsError: '',
  showContent: true,
  lockedId: '',
  crossScene: false,
})

const emit = defineEmits<{ change: [] }>()

const PLACEMENTS = [
  { title: '左上（x=距左、y=距顶）', value: 'tl' },
  { title: '右上（x=距右、y=距顶）', value: 'tr' },
  { title: '左下（x=距左、y=距底）', value: 'bl' },
  { title: '右下（x=距右、y=距底）', value: 'br' },
]

const KIND_LABELS: Record<string, string> = {
  color: '纯色',
  image: '图片',
  video: '视频',
  text: '文本',
  component: '组件',
  iframe: '网页',
}

const RESIZE_LABELS: Record<ResizeConstraint, string> = {
  free: '自由（8 向）',
  ratio: '等比（角手柄）',
  width: '只改宽度（左右边）',
  none: '禁止缩放（只能移动）',
}

const kindLabel = computed(() => KIND_LABELS[props.form.kind] ?? props.form.kind)

const resizeDefault = computed<ResizeConstraint>(() => {
  if (props.form.kind === 'component') {
    return COMPONENT_RESIZE[props.form.name] ?? 'free'
  }
  return 'free'
})
const resizeDefaultLabel = computed(() => RESIZE_LABELS[resizeDefault.value])
const resizeItems = computed(() => [
  { title: `默认（${resizeDefaultLabel.value}）`, value: '' },
  ...(['free', 'ratio', 'width', 'none'] as ResizeConstraint[]).map(value => ({
    title: RESIZE_LABELS[value],
    value,
  })),
])

// ---------- 资产选择器 / 上传（第 5 期：素材走后端 /ui/assets） ----------

/** 本组件的内容区只服务 stream 元素（neuro 内部层 showContent=false）；
 *  dashboard SPA 没有 data-module-base，模块 API 用 vedal 代理的显式基址。 */
function streamApi (path: string): string {
  return new URL(`/manage/stream/${path.replace(/^\/+/, '')}`, window.location.origin).href
}

const fileInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const assetLoading = ref(false)
const assetItems = ref<{ title: string, value: string }[]>([])

function assetKindWanted (): string {
  return props.form.kind === 'video' ? 'video' : 'image'
}

async function refreshAssets (): Promise<void> {
  if (props.form.kind !== 'image' && props.form.kind !== 'video') return
  assetLoading.value = true
  try {
    const resp = await fetch(streamApi('ui/assets'), { cache: 'no-store' })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = (await resp.json()) as { assets?: Array<{ name: string, kind: string }> }
    const want = assetKindWanted()
    assetItems.value = (data.assets ?? [])
      .filter(a => a.kind === want)
      .map(a => ({ title: a.name, value: a.name }))
  } catch (error) {
    console.warn('[studio] 资产清单读取失败', error)
    assetItems.value = []
  } finally {
    assetLoading.value = false
  }
}

onMounted(refreshAssets)
watch(() => props.form.kind, refreshAssets)

function onPickAsset (value: string | null): void {
  if (!value) return
  props.form.src = value
  emit('change')
}

async function uploadTo (body: FormData): Promise<void> {
  uploading.value = true
  try {
    const resp = await fetch(streamApi('ui/assets'), { method: 'POST', body })
    const data = (await resp.json()) as { src?: string, detail?: string }
    if (!resp.ok || !data.src) {
      throw new Error(data.detail ?? `HTTP ${resp.status}`)
    }
    props.form.src = data.src
    emit('change')
    void refreshAssets()
  } catch (error) {
    console.error('[studio] 素材导入失败', error)
    alert(`素材导入失败：${error instanceof Error ? error.message : error}`)
  } finally {
    uploading.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

async function onUploadFile (ev: Event): Promise<void> {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const body = new FormData()
  body.append('file', file)
  await uploadTo(body)
}

async function importFromUrl (): Promise<void> {
  const url = window.prompt('素材外链 URL（http/https；后端抓取转存，不直接引用外链）')
  if (!url) return
  const body = new FormData()
  body.append('url', url)
  await uploadTo(body)
}

/** x/y 的语义随"定位方式"变：edge 表示从哪条边量起。 */
const xFieldLabel = computed(() => {
  const right = props.form.placement === 'tr' || props.form.placement === 'br'
  return right ? 'X · 距右缘（%）' : 'X · 距左缘（%）'
})
const yFieldLabel = computed(() => {
  const bottom = props.form.placement === 'bl' || props.form.placement === 'br'
  return bottom ? 'Y · 距底缘（%）' : 'Y · 距顶缘（%）'
})
</script>
