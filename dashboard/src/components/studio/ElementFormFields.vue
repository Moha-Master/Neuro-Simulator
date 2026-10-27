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
    <v-select
      v-model="form.kind"
      :items="CONTENT_KINDS"
      label="kind"
      variant="outlined"
      density="compact"
      class="mb-2"
      @update:model-value="emit('change')"
    />
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
        label="素材地址（src）"
        variant="outlined"
        density="compact"
        @update:model-value="emit('change')"
      />
    </template>
    <template v-else-if="form.kind === 'text'">
      <v-text-field
        v-model="form.text"
        label="文本（text）"
        variant="outlined"
        density="compact"
        @update:model-value="emit('change')"
      />
    </template>
    <template v-else-if="form.kind === 'component'">
      <v-text-field
        v-model="form.name"
        label="组件名（name）"
        placeholder="chat.overlay"
        variant="outlined"
        density="compact"
        class="mb-2"
        @update:model-value="emit('change')"
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
import { computed } from 'vue'

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
const CONTENT_KINDS = ['color', 'image', 'video', 'text', 'component']

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
