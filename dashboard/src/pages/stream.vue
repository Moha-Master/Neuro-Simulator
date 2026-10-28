<route lang="json">
{
  "meta": { "fullBleed": true }
}
</route>

<template>
  <div class="studio-page">
    <div v-if="!status.checked.value" class="studio-loading">
      <v-progress-circular indeterminate size="48" width="4" />
    </div>

    <template v-else>
      <v-alert
        v-if="lastError"
        type="warning"
        variant="tonal"
        density="compact"
        class="mb-2"
      >
        {{ lastError.message }}
        <div class="text-caption mt-1 text-medium-emphasis">
          neuro 场景 {{ lastError.detail?.neuro_scene ?? '—' }} · 轮询
          {{ lastError.detail?.polls ?? 0 }} 次
          <template v-if="lastError.detail?.status_error">
            · 状态读取失败：{{ lastError.detail.status_error }}
          </template>
        </div>
      </v-alert>

      <!-- 画布：合成器整页（OBS 源同款），letterbox 黑边由 iframe 内部处理 -->
      <div class="studio-canvas">
        <ModuleOffline
          v-if="!status.reachable.value"
          module-name="stream"
          :on-started="status.check"
        />
        <iframe
          v-else
          ref="canvasFrame"
          class="studio-canvas__iframe"
          src="/ui/stream/"
          allow="autoplay"
          title="Stream canvas"
        />
        <div v-if="status.reachable.value" class="studio-canvas__toolbar">
          <v-btn
            icon="mdi-cursor-move"
            size="small"
            variant="flat"
            :color="editMode ? 'primary' : undefined"
            :title="editMode ? '退出编辑模式' : '编辑模式：点选 / 拖动 / 边缘吸附'"
            @click="toggleEditMode"
          />
        </div>
      </div>

      <!-- dock：五张操作卡，内部滚动，不把页面撑出视口 -->
      <div class="studio-dock">
        <!-- 场景：OBS 场景列表（行式、当前高亮、选中态） -->
        <v-card variant="outlined" class="studio-card">
          <v-card-title class="studio-card__title">
            场景
            <v-spacer />
            <v-btn
              icon="mdi-restore"
              size="x-small"
              variant="text"
              title="恢复默认场景清单"
              @click="resetDialog = true"
            />
          </v-card-title>
          <v-card-text class="studio-card__body studio-card__body--flush">
            <div
              v-for="s in st.scenes.value"
              :key="s.id"
              class="studio-scene-row"
              :class="{
                'studio-scene-row--current': s.id === displayedSceneId,
                'studio-scene-row--selected': s.id === selectedSceneId && s.id !== displayedSceneId,
              }"
              @click="selectSceneRow(s.id)"
              @contextmenu.prevent.stop="openSceneMenu($event, s.id)"
            >
              <v-icon size="16">{{ s.icon || 'mdi-image-frame' }}</v-icon>
              <span class="text-truncate">{{ s.label || s.id }}</span>
            </div>
            <div v-if="!st.scenes.value.length" class="text-caption text-disabled py-1">
              场景清单加载中…
            </div>
          </v-card-text>
          <div class="studio-card__actions">
            <v-btn icon="mdi-plus" size="x-small" variant="text" title="新增场景" @click="openCreateScene" />
            <v-btn
              icon="mdi-delete"
              size="x-small"
              variant="text"
              title="删除选中场景"
              :disabled="!canDeleteScene"
              @click="openDeleteScene"
            />
            <v-btn
              icon="mdi-filter"
              size="x-small"
              variant="text"
              title="滤镜（即将推出）"
              disabled
            />
            <v-btn
              icon="mdi-arrow-up"
              size="x-small"
              variant="text"
              title="上移场景"
              :disabled="!canMoveScene(-1)"
              @click="moveScene(-1)"
            />
            <v-btn
              icon="mdi-arrow-down"
              size="x-small"
              variant="text"
              title="下移场景"
              :disabled="!canMoveScene(1)"
              @click="moveScene(1)"
            />
          </div>
        </v-card>

        <!-- 元素：OBS 源列表（行式、眼睛=运行时强制隐藏、齿轮=设置） -->
        <v-card variant="outlined" class="studio-card">
          <v-card-title class="studio-card__title">
            元素
            <v-spacer />
            <v-btn
              icon="mdi-restore"
              size="x-small"
              variant="text"
              title="恢复默认场景清单"
              @click="resetDialog = true"
            />
          </v-card-title>
          <v-card-text class="studio-card__body studio-card__body--flush">
            <template v-for="row in elementRows" :key="row.id">
              <div
                class="studio-element-row"
                :class="{
                  'studio-element-row--selected': isSelectedElement('element', row.id),
                  'studio-element-row--readonly': !row.visible,
                }"
                @click="selectElement('element', row.id)"
              >
                <span
                  v-if="row.color"
                  class="studio-color-block"
                  :style="{ background: row.color }"
                />
                <v-icon v-else size="16">{{ row.icon }}</v-icon>
                <span class="text-truncate flex-grow-1">
                  {{ row.name }}
                  <span v-if="!row.visible" class="text-disabled">· 本场景不显示</span>
                </span>
                <v-btn
                  :icon="hiddenIds.includes(row.id) ? 'mdi-eye-off' : 'mdi-eye'"
                  size="x-small"
                  variant="text"
                  density="compact"
                  :disabled="!row.visible"
                  :title="row.visible
                    ? '关闭 = 合成器层强制隐藏（只隐藏，不强制显示脚本控制的层）'
                    : '本场景不显示'"
                  @click.stop="setElementHidden(row.id, !hiddenIds.includes(row.id))"
                />
              </div>
            </template>

            <!-- [neuro 窗口] 伪源：可选中、可打齿轮；visible=false 时眼睛置灰 -->
            <div
              class="studio-element-row"
              :class="{
                'studio-element-row--selected': isSelectedElement('neuro-window', NEURO_WINDOW_ID),
                'studio-element-row--readonly': !neuroWindowVisible,
              }"
              @click="selectElement('neuro-window', NEURO_WINDOW_ID)"
            >
              <v-icon size="16">mdi-monitor</v-icon>
              <span class="text-truncate flex-grow-1">
                [neuro 窗口]
                <span v-if="!neuroWindowVisible" class="text-disabled">· 本场景不显示</span>
              </span>
              <v-btn
                :icon="hiddenIds.includes(NEURO_WINDOW_ID) ? 'mdi-eye-off' : 'mdi-eye'"
                size="x-small"
                variant="text"
                density="compact"
                :disabled="!neuroWindowVisible"
                :title="neuroWindowVisible
                  ? '关闭 = 合成器层强制隐藏（只隐藏，不强制显示脚本控制的层）'
                  : '本场景不显示'"
                @click.stop="setElementHidden(NEURO_WINDOW_ID, !hiddenIds.includes(NEURO_WINDOW_ID))"
              />
            </div>

            <!-- neuro 内部层不再作为顶层行：她们是"[neuro 窗口] 采集源"的内容，
                 编辑入口在 neuro 窗口对话框的"内部层"区（OBS 窗口捕获语义）。 -->

            <div
              v-if="!elementRows.length"
              class="text-caption text-disabled py-1"
            >
              当前场景没有元素。
            </div>
          </v-card-text>
          <div class="studio-card__actions">
            <v-btn
              icon="mdi-plus"
              size="x-small"
              variant="text"
              title="新增元素（即将推出）"
              disabled
            />
            <v-btn
              icon="mdi-delete"
              size="x-small"
              variant="text"
              title="删除元素（即将推出）"
              disabled
            />
            <v-btn
              icon="mdi-cog"
              size="x-small"
              variant="text"
              :title="selectedElement ? '设置所选元素' : '先在列表中单击选中一个元素'"
              :disabled="!selectedElement"
              @click="openSelectedSettings"
            />
            <v-btn
              icon="mdi-arrow-up"
              size="x-small"
              variant="text"
              title="上移一层（z 变大）"
              :disabled="!canMoveElement(-1)"
              @click="moveElement(-1)"
            />
            <v-btn
              icon="mdi-arrow-down"
              size="x-small"
              variant="text"
              title="下移一层（z 变小）"
              :disabled="!canMoveElement(1)"
              @click="moveElement(1)"
            />
          </div>
        </v-card>

        <!-- 混音器 -->
        <v-card variant="outlined" class="studio-card">
          <v-card-title class="studio-card__title">混音器</v-card-title>
          <v-card-text class="studio-card__body">
            <div class="d-flex align-center ga-2">
              <v-icon size="small">mdi-volume-high</v-icon>
              <v-slider
                v-model="volumePct"
                :min="0"
                :max="100"
                step="1"
                density="compact"
                hide-details
                class="flex-grow-1"
                @update:model-value="onVolume"
              />
              <span class="text-caption" style="width: 3em">{{ volumePct }}</span>
            </div>
            <v-switch
              v-model="muted"
              label="静音"
              color="primary"
              density="compact"
              hide-details
              @update:model-value="onVolume"
            />
            <div class="text-caption text-disabled mt-2">
              控制 neuro 窗口语音音量（合成器无独立音频）。
            </div>
          </v-card-text>
        </v-card>

        <!-- 转场（占位） -->
        <v-card variant="outlined" class="studio-card">
          <v-card-title class="studio-card__title">转场</v-card-title>
          <v-card-text class="studio-card__body">
            <v-select
              :items="['淡入', '推移', '无']"
              model-value="无"
              label="转场效果"
              variant="outlined"
              density="compact"
              disabled
              hide-details
            />
            <div class="text-caption text-disabled mt-2">全局转场效果：即将推出</div>
          </v-card-text>
        </v-card>

        <!-- 控制：极简按钮组（状态 chip / 说明文字已移除） -->
        <v-card variant="outlined" class="studio-card">
          <v-card-title class="studio-card__title">控制</v-card-title>
          <v-card-text class="studio-card__body d-flex flex-column ga-2">
            <template v-if="isRunning">
              <div class="d-flex ga-2">
                <v-btn
                  class="flex-grow-1"
                  :color="currentState === 'paused' ? 'info' : 'warning'"
                  variant="tonal"
                  :prepend-icon="currentState === 'paused' ? 'mdi-play' : 'mdi-pause'"
                  :disabled="currentState === 'starting'"
                  @click="doAction(currentState === 'paused' ? 'resume' : 'pause')"
                >
                  {{ currentState === 'paused' ? '继续' : '暂停' }}
                </v-btn>
                <v-btn
                  class="flex-grow-1"
                  color="error"
                  variant="tonal"
                  prepend-icon="mdi-stop"
                  @click="doAction('stop')"
                >
                  停止
                </v-btn>
              </div>
              <v-btn block variant="tonal" prepend-icon="mdi-record-rec" disabled>
                模拟直播
              </v-btn>
              <v-btn block variant="tonal" prepend-icon="mdi-cog-outline" @click="openSettings">
                设置
              </v-btn>
            </template>
            <template v-else>
              <v-btn
                block
                color="success"
                prepend-icon="mdi-broadcast"
                :loading="st.busy.value"
                @click="start(false)"
              >
                开始直播
              </v-btn>
              <v-btn
                block
                color="warning"
                variant="tonal"
                prepend-icon="mdi-record-rec"
                :loading="st.busy.value"
                @click="start(true)"
              >
                模拟直播
              </v-btn>
              <v-btn block variant="tonal" prepend-icon="mdi-cog-outline" @click="openSettings">
                设置
              </v-btn>
            </template>
          </v-card-text>
        </v-card>
      </div>
    </template>

    <!-- 场景右键菜单 -->
    <div v-if="sceneMenu.open" class="studio-menu-backdrop" @click="sceneMenu.open = false" />
    <div
      v-if="sceneMenu.open"
      class="studio-menu"
      :style="{ left: `${sceneMenu.x}px`, top: `${sceneMenu.y}px` }"
      @click.stop
    >
      <v-list density="compact" nav>
        <v-list-item @click="openSceneProperties(sceneMenu.sceneId)">
          <v-list-item-title>属性</v-list-item-title>
        </v-list-item>
      </v-list>
    </div>

    <!-- 新增场景 -->
    <v-dialog v-model="createOpen" max-width="520">
      <v-card>
        <v-card-title>新增场景</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="createForm.id"
            label="场景 id（必填）"
            placeholder="test_scene"
            variant="outlined"
            density="compact"
            class="mb-2"
            :error-messages="createIdError ? [createIdError] : []"
          />
          <v-text-field
            v-model="createForm.label"
            label="名称（label）"
            variant="outlined"
            density="compact"
            class="mb-2"
          />
          <v-text-field
            v-model="createForm.desc"
            label="机器/LLM 自述（desc）"
            variant="outlined"
            density="compact"
            class="mb-2"
          />
          <v-text-field
            v-model="createForm.icon"
            label="图标（mdi 名）"
            placeholder="mdi-image-frame"
            variant="outlined"
            density="compact"
            class="mb-2"
          />
          <v-checkbox
            v-model="createForm.cloneCurrent"
            label="克隆当前场景（elements + neuro 块）"
            density="compact"
            hide-details
          />
          <div class="text-caption text-disabled mt-2">
            不勾选则创建最小合法场景（elements 为空 + neuro 窗口）。
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="createOpen = false">取消</v-btn>
          <v-btn color="primary" :disabled="!canCreateScene" @click="createScene">创建</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 删除场景确认 -->
    <v-dialog :model-value="deleteOpen" max-width="480" @update:model-value="deleteOpen = false">
      <v-card>
        <v-card-title>删除场景</v-card-title>
        <v-card-text>
          确定删除「{{ deleteSceneLabel }}」？删除当前画面所在场景也可以，循环再切到它时画面会保持不动。
          <div class="text-caption text-disabled mt-2">
            误删可用右上角重置恢复默认清单。
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="deleteOpen = false">取消</v-btn>
          <v-btn color="error" variant="tonal" @click="deleteScene">删除</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 重置默认清单确认（场景卡 / 元素卡共用） -->
    <v-dialog v-model="resetDialog" max-width="480">
      <v-card>
        <v-card-title>重置场景清单</v-card-title>
        <v-card-text>
          确定恢复默认场景清单？当前所有场景改动（含自定义场景/元素设置）都会丢失。
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="resetDialog = false">取消</v-btn>
          <v-btn color="error" variant="tonal" @click="resetManifest">恢复默认</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 场景属性 -->
    <v-dialog v-model="scenePropsOpen" max-width="720" scrollable>
      <v-card>
        <v-card-title class="d-flex align-center">
          场景属性
          <v-chip v-if="scenePropsId" class="ml-2" size="x-small" variant="tonal">
            {{ scenePropsId }}
          </v-chip>
        </v-card-title>
        <v-card-text>
          <v-alert
            v-if="manifestError"
            type="error"
            variant="tonal"
            density="compact"
            class="mb-3"
          >
            <pre class="text-wrap">{{ manifestError }}</pre>
          </v-alert>

          <v-text-field
            v-model="sceneForm.label"
            label="名称（label）"
            variant="outlined"
            density="compact"
            class="mb-2"
            @update:model-value="applySceneBasics"
          />
          <v-text-field
            v-model="sceneForm.desc"
            label="机器/LLM 自述（desc）"
            variant="outlined"
            density="compact"
            class="mb-2"
            @update:model-value="applySceneBasics"
          />
          <v-text-field
            v-model="sceneForm.icon"
            label="图标（mdi 名）"
            variant="outlined"
            density="compact"
            class="mb-2"
            @update:model-value="applySceneBasics"
          />
          <v-text-field
            v-model="sceneForm.duration"
            label="时长（秒，留空 = 不设）"
            type="number"
            variant="outlined"
            density="compact"
            class="mb-2"
            @update:model-value="applySceneBasics"
          />
          <v-textarea
            v-model="sceneForm.prompt_context"
            label="prompt_context（留空 = 删键）"
            variant="outlined"
            density="compact"
            rows="3"
            class="mb-2"
            @update:model-value="applySceneBasics"
          />
          <v-switch
            v-model="sceneForm.highlight_messages"
            label="允许高亮消息（highlight_messages）"
            color="primary"
            density="compact"
            hide-details
            class="mb-2"
            @update:model-value="applySceneBasics"
          />
          <v-textarea
            v-model="sceneForm.timeline"
            label="timeline JSON（空 = 不带该键）"
            :error-messages="timelineError ? [timelineError] : []"
            variant="outlined"
            density="compact"
            rows="5"
            spellcheck="false"
            class="studio-json mt-2"
            @update:model-value="applySceneTimeline"
          />
          <v-textarea
            v-model="sceneForm.state_script"
            label="state_script JSON（空 = 不带该键）"
            :error-messages="stateScriptError ? [stateScriptError] : []"
            variant="outlined"
            density="compact"
            rows="5"
            spellcheck="false"
            class="studio-json"
            @update:model-value="applySceneStateScript"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="closeSceneProps">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 元素设置 -->
    <v-dialog v-model="elementDialog" max-width="720" scrollable>
      <v-card>
        <v-card-title class="d-flex align-center">
          元素设置
          <v-chip class="ml-2" size="x-small" variant="tonal">{{ elementTargetId }}</v-chip>
        </v-card-title>
        <v-card-text>
          <v-alert
            v-if="elementDialogError"
            type="error"
            variant="tonal"
            density="compact"
            class="mb-3"
          >
            <pre class="text-wrap">{{ elementDialogError }}</pre>
          </v-alert>

          <ElementFormFields
            :form="elementForm"
            :script-error="scriptError"
            :props-error="propsError"
            :locked-id="elementTargetId"
            @change="applyElementForm"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="closeElementDialog">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- neuro 窗口（采集源）：窗口摆放 + 内部层内容（写 neuro 自己的清单） -->
    <v-dialog v-model="neuroDialog" max-width="720" scrollable>
      <v-card>
        <v-card-title>neuro 窗口</v-card-title>
        <v-card-text>
          <v-alert
            v-if="manifestError || neuroManifestError"
            type="error"
            variant="tonal"
            density="compact"
            class="mb-3"
          >
            <pre class="text-wrap">{{ manifestError || neuroManifestError }}</pre>
          </v-alert>

          <div class="text-subtitle-2 mb-1">窗口</div>
          <div class="text-caption text-medium-emphasis mb-2">
            坐标相对画面（舞台）尺寸，100 = 满屏；负值可把窗口移出画面。
          </div>
          <v-row dense>
            <v-col cols="4">
              <v-text-field
                v-model="neuroForm.x"
                label="X · 距左缘（%）"
                type="number"
                variant="outlined"
                density="compact"
                hide-details
                @update:model-value="applyNeuroForm"
              />
            </v-col>
            <v-col cols="4">
              <v-text-field
                v-model="neuroForm.y"
                label="Y · 距顶缘（%）"
                type="number"
                variant="outlined"
                density="compact"
                hide-details
                @update:model-value="applyNeuroForm"
              />
            </v-col>
            <v-col cols="4">
              <v-text-field
                v-model="neuroForm.w"
                label="宽度（%画面宽）"
                type="number"
                variant="outlined"
                density="compact"
                hide-details
                @update:model-value="applyNeuroForm"
              />
            </v-col>
            <v-col cols="6">
              <v-text-field
                v-model="neuroForm.z"
                label="层级（越大越靠前，空=默认）"
                type="number"
                variant="outlined"
                density="compact"
                hide-details
                @update:model-value="applyNeuroForm"
              />
            </v-col>
            <v-col cols="6">
              <v-switch
                v-model="neuroForm.hidden"
                label="整窗隐藏（neuro.hidden）"
                color="primary"
                density="compact"
                hide-details
                @update:model-value="applyNeuroForm"
              />
            </v-col>
          </v-row>

          <div class="text-subtitle-2 mt-3 mb-1">隐藏内部层（neuro.hide）</div>
          <v-select
            v-model="neuroForm.hide"
            :items="neuroHideItems"
            item-title="title"
            item-value="value"
            label="勾选 = 写进当前场景的 neuro.hide"
            variant="outlined"
            density="compact"
            multiple
            chips
            @update:model-value="applyNeuroForm"
          />
          <div class="text-caption text-disabled">
            这是数据层声明（画面 reload 生效），与眼睛按钮的运行时通道并存。
          </div>

          <v-divider class="my-3" />
          <div class="text-subtitle-2 mb-1">内部层（neuro 窗口的内容 · 写入 neuro 自己的清单）</div>
          <template v-if="neuroLayerRows.length">
            <v-expansion-panels
              v-model="neuroExpandedLayer"
              variant="accordion"
              flat
              density="compact"
              @update:model-value="onLayerExpand"
            >
              <v-expansion-panel
                v-for="layer in neuroLayerRows"
                :key="layer.layerId"
                :value="layer.layerId"
              >
                <v-expansion-panel-title>
                  <div class="d-flex align-center w-100">
                    <v-icon size="16" class="mr-2">mdi-layers</v-icon>
                    <span class="flex-grow-1">{{ layer.name }}</span>
                    <span v-if="!layer.visible" class="text-caption text-disabled mr-2">
                      本场景不显示
                    </span>
                    <v-btn
                      :icon="hiddenIds.includes(layer.sourceId) ? 'mdi-eye-off' : 'mdi-eye'"
                      size="x-small"
                      variant="text"
                      density="compact"
                      :disabled="!layer.visible"
                      :title="layer.visible
                        ? '关闭 = 运行时强制隐藏（不改数据）'
                        : '本场景不显示'"
                      @click.stop="setElementHidden(layer.sourceId, !hiddenIds.includes(layer.sourceId))"
                    />
                  </div>
                </v-expansion-panel-title>
                <v-expansion-panel-text>
                  <ElementFormFields
                    v-if="layer.editable && neuroExpandedLayer === layer.layerId"
                    :form="elementForm"
                    :script-error="scriptError"
                    :props-error="propsError"
                    :show-content="false"
                    cross-scene
                    @change="applyElementForm"
                  />
                  <div v-else class="text-caption text-disabled py-1">
                    neuro 清单未到达，暂不可编辑。
                  </div>
                </v-expansion-panel-text>
              </v-expansion-panel>
            </v-expansion-panels>
          </template>
          <div v-else-if="!neuroScenes" class="text-caption text-disabled py-1">
            neuro 模块不可达，内部层暂不可用。
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="closeNeuroDialog">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 设置：running 改本场（PUT /stream/metadata）；停止时改 config 默认值 -->
    <v-dialog v-model="settingsOpen" max-width="560">
      <v-card>
        <v-card-title>设置</v-card-title>
        <v-card-text>
          <v-alert
            v-if="settingsError"
            type="error"
            variant="tonal"
            density="compact"
            class="mb-3"
          >
            {{ settingsError }}
          </v-alert>
          <v-text-field
            v-model="formTitle"
            label="标题（title）"
            variant="outlined"
            density="compact"
            counter="120"
            class="mb-2"
          />
          <v-combobox
            v-model="formCategory"
            :items="CATEGORIES"
            label="分区（category）"
            variant="outlined"
            density="compact"
            clearable
            class="mb-2"
          />
          <div class="text-caption text-medium-emphasis mb-1">标签（tags）</div>
          <TagInput v-model="formTags" />
          <template v-if="isRunning">
            <v-divider class="my-3" />
            <div class="d-flex align-center flex-wrap ga-2">
              <v-switch
                :model-value="!highlightPaused"
                :label="highlightPaused ? '高亮消息队列：暂停' : '高亮消息队列：正常'"
                color="primary"
                density="compact"
                hide-details
                :disabled="!highlightControllable"
                @update:model-value="onHighlightToggle"
              />
              <v-chip size="small" variant="tonal" prepend-icon="mdi-source-branch">
                来源：{{ highlightSourceLabel }}
              </v-chip>
            </div>
            <div class="text-caption text-disabled">
              暂停时高亮轨不消费，普通消息照常。
            </div>
          </template>
          <div class="text-caption text-disabled mt-2">
            {{
              isRunning
                ? '保存 = 立即应用到当前场次（PUT /stream/metadata）。'
                : '保存 = 写入 config 的 stream.default_*，作为下一场初值。'
            }}
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" :disabled="settingsBusy" @click="settingsOpen = false">取消</v-btn>
          <v-btn color="primary" :loading="settingsBusy" @click="saveSettings">保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="snackbar" :timeout="5000" color="error" location="bottom">
      {{ snackbarText }}
    </v-snackbar>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'

import type { ElementFormState } from '@/components/studio/ElementFormFields.vue'
import ModuleOffline from '@/components/ModuleOffline.vue'
import TagInput from '@/components/tags/TagInput.vue'
import { useModuleStatus } from '@/composables/useModuleStatus'
import { useStream } from '@/composables/useStream'
import { api, describeError } from '@/stores/connection'

const status = useModuleStatus('stream')
const st = useStream()

const CATEGORIES = [
  'Just Chatting',
  'IRL',
  'Music',
  'Art',
  'Minecraft',
  'osu!',
  'Chess',
  'Talking',
]

// ---------- 画布 ⇄ 合成器（postMessage；字段名与 ui/stream/main.ts 逐字一致） ----------

const canvasFrame = ref<HTMLIFrameElement | null>(null)
const canvasReady = ref(false)
/** 编辑模式：只在被 Studio 嵌入的画布里激活；OBS 直挂没有父页面，永不激活。 */
const editMode = ref(false)
/** 合成器元素/层的强制隐藏集合（只隐藏，不能强制显示）。
 *  命名空间：场景元素 id、`neuro.window`、`neuro.layer:<id>`（见 ui/stream/main.ts）。 */
const hiddenIds = ref<string[]>([])

const NEURO_WINDOW_ID = 'neuro.window'
const NEURO_LAYER_PREFIX = 'neuro.layer:'

interface ManifestBox {
  x?: number
  y?: number
  w?: number
  h?: number
  edge?: { x?: 'left' | 'right', y?: 'top' | 'bottom' }
  scale?: number
  rotate?: number
  opacity?: number
}

interface ManifestContent {
  kind?: string
  [key: string]: unknown
}

interface ManifestElement {
  id: string
  name?: string | null
  visible?: boolean
  z?: number
  box?: ManifestBox
  content?: ManifestContent
  script?: Record<string, unknown>
  css?: Record<string, string>
}

interface ManifestNeuro {
  x?: number
  y?: number
  w?: number
  z?: number
  hidden?: boolean
  hide?: string[]
  edge?: { x?: 'left' | 'right', y?: 'top' | 'bottom' }
}

interface ManifestScene {
  id: string
  label?: string
  desc?: string
  icon?: string
  duration?: number
  prompt_context?: string
  highlight_messages?: boolean
  elements?: ManifestElement[]
  neuro?: ManifestNeuro
  timeline?: Record<string, unknown>
  state_script?: Record<string, unknown>
}

interface StreamManifest {
  version?: number
  scenes: ManifestScene[]
}

interface NeuroSceneElement {
  id: string
  name?: string | null
  z?: number | null
  visible?: boolean
  content?: string | null
  component?: string | null
}

interface NeuroSceneSummary {
  id: string
  label?: string
  elements: NeuroSceneElement[]
}

interface NeuroScenesResponse {
  scenes: NeuroSceneSummary[]
  current?: { id?: string } | null
}

/** neuro 完整清单里的元素（结构与 stream 元素同构：box + z + script）。 */
interface NeuroManifestElement {
  id: string
  name?: string | null
  visible?: boolean
  z?: number
  box?: ManifestBox
  content?: ManifestContent
  script?: Record<string, unknown>
  css?: Record<string, string>
}

interface NeuroManifestScene {
  id: string
  label?: string
  desc?: string
  icon?: string
  duration?: number | Record<string, unknown>
  highlight_messages?: boolean
  elements?: NeuroManifestElement[]
  timeline?: Record<string, unknown>
  state_script?: Record<string, unknown>
}

interface NeuroManifest {
  version?: number
  default_scene?: string
  refs?: Record<string, string>
  scenes: NeuroManifestScene[]
}

interface SceneMenuState {
  open: boolean
  x: number
  y: number
  sceneId: string
}

const AUDIO_VOLUME_KEY = 'ns.audio.volume'
const AUDIO_MUTED_KEY = 'ns.audio.muted'

function readStoredVolume(): number {
  const raw = Number(localStorage.getItem(AUDIO_VOLUME_KEY))
  return Number.isFinite(raw) ? Math.min(1, Math.max(0, raw)) : 1
}

const volume = ref(readStoredVolume())
const muted = ref(localStorage.getItem(AUDIO_MUTED_KEY) === '1')
const volumePct = ref(Math.round(volume.value * 100))

function postCanvas(payload: Record<string, unknown>): void {
  // stream-canvas 通道收紧为同源：Studio 与画布始终同源（vedal 静态托管）。
  canvasFrame.value?.contentWindow?.postMessage(payload, window.location.origin)
}

function sendVisibility(): void {
  if (!canvasReady.value) return
  // Vue 的 ref 数组是 Proxy，structured clone 会抛 DataCloneError：
  // postMessage 必须传普通数组副本。
  postCanvas({ ns: 'stream-canvas', type: 'visibility', hidden: [...hiddenIds.value] })
}

function sendVolume(): void {
  if (!canvasReady.value) return
  postCanvas({ ns: 'stream-canvas', type: 'volume', value: volume.value, muted: muted.value })
}

function sendEdit(active: boolean): void {
  if (!canvasReady.value) return
  postCanvas({ ns: 'stream-canvas', type: 'edit', active })
}

function toggleEditMode(): void {
  editMode.value = !editMode.value
  sendEdit(editMode.value)
}

/** 切场景 / 删场景 / 重置前先退出编辑模式（画布会丢弃预览与选中）。 */
function exitCanvasEdit(): void {
  if (!editMode.value) return
  editMode.value = false
  sendEdit(false)
}

function onCanvasMessage(event: MessageEvent): void {
  // 只认同源、只认自己 iframe（其余消息如 neuro 转发的帧一概不处理）
  if (event.origin !== window.location.origin) return
  if (event.source !== canvasFrame.value?.contentWindow) return
  const data = event.data as
    | {
      ns?: string
      type?: string
      kind?: string | null
      id?: string | null
      sceneId?: string
      active?: boolean
      box?: ManifestBox
    }
    | undefined
  if (data?.ns !== 'stream-canvas') return

  if (data.type === 'ready') {
    canvasReady.value = true
    // iframe 装载前父页面就持有状态：ready 后补发
    sendVisibility()
    sendVolume()
    sendEdit(editMode.value)
    return
  }
  if (data.type === 'select') {
    if (
      (data.kind === 'element' || data.kind === 'neuro-window')
      && typeof data.id === 'string'
      && data.id
    ) {
      // 直接赋值（不能再走 selectElement：那会 flush + 回发消息，形成回环）
      selectedElement.value = { kind: data.kind, id: data.id }
    } else {
      selectedElement.value = null
    }
    return
  }
  if (data.type === 'preview' || data.type === 'commit') {
    if (!data.box || typeof data.id !== 'string' || !data.id) return
    if (data.type === 'preview') {
      applyCanvasPreview(data.kind, data.id, data.box)
    } else {
      applyCanvasCommit(data.sceneId, data.kind, data.id, data.box)
    }
  }
}

/** 拖动中间态：只更新打开着的表单显示值，不写 manifest。 */
function applyCanvasPreview(
  kind: string | null | undefined,
  id: string,
  box: ManifestBox,
): void {
  if (elementDialog.value && elementTargetId.value === id && kind === 'element') {
    elementForm.x = pctValue(box.x ?? 0)
    elementForm.y = pctValue(box.y ?? 0)
    if (box.edge?.x || box.edge?.y) elementForm.placement = placementOf(box)
  }
  if (neuroDialog.value && !neuroEditingLayer.value && kind === 'neuro-window') {
    neuroForm.x = pctValue(box.x ?? 0)
    neuroForm.y = pctValue(box.y ?? 0)
  }
}

/** 松手落库：只改本地 manifest 缓存 → 立即 flush（不等 1s 防抖）。 */
function applyCanvasCommit(
  sceneId: string | undefined,
  kind: string | null | undefined,
  id: string,
  box: ManifestBox,
): void {
  const scene = (sceneId ? sceneById(sceneId) : displayedScene.value) ?? displayedScene.value
  if (!scene) return
  if (kind === 'element') {
    const el = scene.elements?.find((e) => e.id === id)
    if (!el) return
    el.box = cloneData(box)
    selectedElement.value = { kind: 'element', id }
  } else if (kind === 'neuro-window') {
    if (!scene.neuro) return
    scene.neuro.x = Number(box.x ?? scene.neuro.x ?? 0)
    scene.neuro.y = Number(box.y ?? scene.neuro.y ?? 0)
    if (box.edge) scene.neuro.edge = cloneData(box.edge)
    selectedElement.value = { kind: 'neuro-window', id }
  } else {
    return
  }
  applyCanvasPreview(kind, id, box)
  queueManifestSave()
  // 拖拽 commit 必须立刻落盘；防抖只留给表单输入。
  void flushManifestSave()
}

function onVolume(): void {
  volume.value = volumePct.value / 100
  localStorage.setItem(AUDIO_VOLUME_KEY, String(volume.value))
  localStorage.setItem(AUDIO_MUTED_KEY, muted.value ? '1' : '0')
  sendVolume()
}

function setElementHidden(id: string, hidden: boolean): void {
  const next = new Set(hiddenIds.value)
  if (hidden) next.add(id)
  else next.delete(id)
  hiddenIds.value = [...next]
  sendVisibility()
}

// ---------- 状态 / 场景 ----------

const currentState = computed(
  () => st.status.value?.sections.loop.current?.state ?? 'offline',
)
const isRunning = computed(() => ['starting', 'live', 'paused'].includes(currentState.value))
const lastError = computed(() => st.status.value?.sections.loop.current?.last_error ?? null)

const currentScene = computed(() => st.status.value?.sections.scene.current?.id ?? '')

/** 合成器的完整场景清单（含 starting_soon/开场）：保存 = 防抖 PUT /ui/manifest。 */
const streamManifest = ref<StreamManifest | null>(null)

/** neuro 当前场景的内部层（来源：GET /manage/neuro_sama/ui/scenes）。 */
const neuroScenes = ref<NeuroScenesResponse | null>(null)

/** neuro 完整清单（来源：GET /manage/neuro_sama/ui/manifest；内部层的几何在这里）。
 *  neuro 不可达时保持 null，内部层行退回"只给眼睛"的旧行为。 */
const neuroManifest = ref<NeuroManifest | null>(null)

function cloneData<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function sceneById(id: string): ManifestScene | null {
  return streamManifest.value?.scenes.find((s) => s.id === id) ?? null
}

/** 画布实际显示的 stream 场景：状态里的 id 被删了就回落到待机场景。 */
const displayedSceneId = computed(() => {
  const scenes = streamManifest.value?.scenes ?? []
  if (currentScene.value && scenes.some((s) => s.id === currentScene.value)) {
    return currentScene.value
  }
  if (scenes.some((s) => s.id === 'starting_soon')) return 'starting_soon'
  return scenes.find((s) => (s.elements ?? []).some((e) => e.id === 'chat'))?.id
    ?? scenes[0]?.id
    ?? ''
})
const displayedScene = computed(() => sceneById(displayedSceneId.value))

const selectedSceneId = ref('')
// 状态首次到达时把选中行对齐到当前画面；之后尊重用户点击（OBS 点击即选中+切换）。
watch(
  displayedSceneId,
  (id) => {
    if (id && !selectedSceneId.value) selectedSceneId.value = id
  },
  { immediate: true },
)

const selectedSceneRow = computed(() =>
  st.scenes.value.find((s) => s.id === selectedSceneId.value) ?? null,
)
const canDeleteScene = computed(() => st.scenes.value.length > 1 && !!selectedSceneId.value)

function sceneIndex(id: string): number {
  return streamManifest.value?.scenes.findIndex((s) => s.id === id) ?? -1
}

function canMoveScene(delta: number): boolean {
  const idx = sceneIndex(selectedSceneId.value)
  if (idx < 0) return false
  const next = idx + delta
  return next >= 0 && next < (streamManifest.value?.scenes.length ?? 0)
}

/** 场景列表的行顺序跟随 manifest 数组顺序（保存后 st.scenes 立即刷新）。 */
async function selectSceneRow(id: string): Promise<void> {
  selectedSceneId.value = id
  if (id === displayedSceneId.value) return
  exitCanvasEdit()
  await flushManifestSave()
  await switchScene(id)
}

async function switchScene(id: string): Promise<void> {
  await st.setScene(id)
  await st.refreshStatus()
}

// ---------- 元素行（OBS 源列表：顶部 = 最上层 = z 降序） ----------

interface ElementRow {
  id: string
  name: string
  icon: string
  color: string
  z: number
  visible: boolean
}

const ELEMENT_ICONS: Record<string, string> = {
  color: 'mdi-palette',
  image: 'mdi-image',
  video: 'mdi-video',
  text: 'mdi-format-text',
  component: 'mdi-puzzle',
}

const elementRows = computed<ElementRow[]>(() => {
  const elements = displayedScene.value?.elements ?? []
  return elements
    .map((el) => {
      const kind = String(el.content?.kind ?? '')
      return {
        id: el.id,
        name: el.name || el.id,
        icon: ELEMENT_ICONS[kind] ?? 'mdi-cube-outline',
        color: kind === 'color' ? String(el.content?.color ?? '') : '',
        z: Number(el.z ?? 0),
        visible: el.visible !== false,
      }
    })
    .sort((a, b) => b.z - a.z)
})

const neuroWindowVisible = computed(
  () => !!displayedScene.value?.neuro && !displayedScene.value.neuro.hidden,
)

function neuroSceneById(id: string): NeuroManifestScene | null {
  return neuroManifest.value?.scenes.find((s) => s.id === id) ?? null
}

const currentNeuroScene = computed(() => {
  const neuro = neuroScenes.value
  if (!neuro?.current?.id) return null
  return neuro.scenes.find((s) => s.id === neuro.current?.id) ?? null
})
/** neuro 当前场景在完整清单里的那份（内部层几何/脚本来源）。 */
const currentNeuroManifestScene = computed(() => {
  const id = neuroScenes.value?.current?.id
  return id ? neuroSceneById(id) : null
})
const neuroLayerRows = computed(() => {
  const summary = currentNeuroScene.value
  if (!summary) return []
  const fullById = new Map(
    (currentNeuroManifestScene.value?.elements ?? []).map((el) => [el.id, el]),
  )
  return (summary.elements ?? []).map((el) => ({
    sourceId: `${NEURO_LAYER_PREFIX}${el.id}`,
    layerId: el.id,
    name: el.name || el.id,
    // 场景里 visible:false 的层由脚本（如开场时间线）负责显示，外部只能再隐藏。
    visible: el.visible !== false,
    // 完整清单拿到之前只给眼睛（不选中、无齿轮）。
    editable: fullById.has(el.id),
  }))
})

// ---------- 选中元素 ----------

type ElementKind = 'element' | 'neuro-window'

interface ElementSelection {
  kind: ElementKind
  id: string
}

const selectedElement = ref<ElementSelection | null>(null)

function isSelectedElement(kind: ElementKind, id: string): boolean {
  return selectedElement.value?.kind === kind && selectedElement.value.id === id
}

async function selectElement(kind: ElementKind, id: string): Promise<void> {
  if (isSelectedElement(kind, id)) return
  await flushManifestSave()
  await flushNeuroManifestSave()
  selectedElement.value = { kind, id }
  // 双向同步：列表点选 → 画布画选中框（画布的 select 处理只赋值，不回发，避免回环）
  if (editMode.value) {
    postCanvas({ ns: 'stream-canvas', type: 'select', kind, id })
  }
}

// 切场景 = 上一个场景的选中行作废；未保存改动先落盘。
watch(displayedSceneId, () => {
  void flushManifestSave()
  void flushNeuroManifestSave()
  selectedElement.value = null
  elementDialog.value = false
  neuroDialog.value = false
  neuroEditingLayer.value = null
})

function selectedElementDef(): ManifestElement | null {
  const selection = selectedElement.value
  if (!selection || selection.kind !== 'element') return null
  return displayedScene.value?.elements?.find((e) => e.id === selection.id) ?? null
}

function findNeuroLayerEl(layerId: string): NeuroManifestElement | null {
  const inCurrent = currentNeuroManifestScene.value?.elements?.find((e) => e.id === layerId)
  if (inCurrent) return inCurrent
  // neuro 当前场景切走过：同 id 在整个清单里是同一家族，任取一份兜底。
  for (const scene of neuroManifest.value?.scenes ?? []) {
    const el = scene.elements?.find((e) => e.id === layerId)
    if (el) return el
  }
  return null
}

function canMoveElement(delta: number): boolean {
  if (selectedElement.value?.kind !== 'element') return false
  const idx = elementRows.value.findIndex((r) => r.id === selectedElement.value?.id)
  if (idx < 0) return false
  const next = idx + delta
  return next >= 0 && next < elementRows.value.length
}

// 列表顶部 = 最上层（z 降序）：↑ = 与上一行交换 z（变大一层）。
function moveElement(delta: number): void {
  const selection = selectedElement.value
  if (selection?.kind !== 'element') return
  const scene = displayedScene.value
  if (!scene) return
  const idx = elementRows.value.findIndex((r) => r.id === selection.id)
  const next = idx + delta
  if (idx < 0 || next < 0 || next >= elementRows.value.length) return
  const otherId = elementRows.value[next]?.id
  const elements = scene.elements ?? []
  const a = elements.find((e) => e.id === selection.id)
  const b = elements.find((e) => e.id === otherId)
  if (!a || !b) return
  const z = a.z
  a.z = b.z
  b.z = z
  queueManifestSave()
}

// ---------- manifest 本地缓存 + 1s 防抖保存 ----------

const manifestError = ref('')
let manifestDirty = false
let manifestRev = 0
let manifestTimer: number | null = null
let manifestSaving = false

function stringifyManifest(): string {
  return JSON.stringify(streamManifest.value)
}

function queueManifestSave(): void {
  if (!streamManifest.value) return
  manifestDirty = true
  manifestRev += 1
  if (manifestTimer !== null) window.clearTimeout(manifestTimer)
  manifestTimer = window.setTimeout(() => {
    manifestTimer = null
    void flushManifestSave()
  }, 1000)
}

/** 立即落盘未保存改动（关闭对话框 / 切换选中行 / 切场景时调用）。 */
async function flushManifestSave(): Promise<void> {
  if (!manifestDirty || !streamManifest.value) {
    if (manifestTimer !== null) {
      window.clearTimeout(manifestTimer)
      manifestTimer = null
    }
    return
  }
  if (manifestSaving) {
    // 保存中：**不能清掉防抖 timer**，否则这次改动可能永远等不到下一次 flush
    //（拖拽 commit 连发时尤其明显）。timer 到点会再进来。
    if (manifestTimer === null) {
      manifestTimer = window.setTimeout(() => {
        manifestTimer = null
        void flushManifestSave()
      }, 1000)
    }
    return
  }
  if (manifestTimer !== null) {
    window.clearTimeout(manifestTimer)
    manifestTimer = null
  }
  const rev = manifestRev
  const content = stringifyManifest()
  manifestSaving = true
  manifestError.value = ''
  try {
    await api('/manage/stream/ui/manifest', {
      method: 'PUT',
      body: JSON.stringify({ content }),
    })
    if (rev === manifestRev) manifestDirty = false
    await st.refreshScenes()
    if (rev === manifestRev) {
      // 用服务端刚存下的那份刷新缓存，避免 5s 轮询先拿旧数据覆盖新改动。
      await refreshStreamManifest(true)
    }
  } catch (e) {
    manifestError.value = describeError(e)
    showSnack(manifestError.value)
  } finally {
    manifestSaving = false
    if (manifestDirty && rev !== manifestRev) {
      // 保存期间又有新改动：继续合并到下一次
      manifestTimer = window.setTimeout(() => {
        manifestTimer = null
        void flushManifestSave()
      }, 1000)
    }
  }
}

function discardPendingManifest(): void {
  if (manifestTimer !== null) {
    window.clearTimeout(manifestTimer)
    manifestTimer = null
  }
  manifestDirty = false
}

async function refreshStreamManifest(force = false): Promise<void> {
  if (!force && (manifestDirty || manifestSaving)) return
  try {
    const data = await api<StreamManifest>('/manage/stream/ui/manifest')
    if (force || (!manifestDirty && !manifestSaving)) {
      streamManifest.value = data
      if (!selectedSceneId.value) selectedSceneId.value = displayedSceneId.value
    }
  } catch {
    // 模块暂时不可达：保留现有缓存，画面/卡片继续用旧数据
  }
}

async function ensureManifest(): Promise<boolean> {
  if (streamManifest.value) return true
  await refreshStreamManifest(true)
  return !!streamManifest.value
}

// ---------- neuro manifest 本地缓存 + 1s 防抖保存（与 stream 各一份，互不干扰） ----------

const neuroManifestError = ref('')
let neuroManifestDirty = false
let neuroManifestRev = 0
let neuroManifestTimer: number | null = null
let neuroManifestSaving = false

function stringifyNeuroManifest(): string {
  return JSON.stringify(neuroManifest.value)
}

function queueNeuroManifestSave(): void {
  if (!neuroManifest.value) return
  neuroManifestDirty = true
  neuroManifestRev += 1
  if (neuroManifestTimer !== null) window.clearTimeout(neuroManifestTimer)
  neuroManifestTimer = window.setTimeout(() => {
    neuroManifestTimer = null
    void flushNeuroManifestSave()
  }, 1000)
}

/** 立即落盘未保存的 neuro 内部层改动（关闭对话框 / 切换选中行 / 切场景时调用）。 */
async function flushNeuroManifestSave(): Promise<void> {
  if (neuroManifestTimer !== null) {
    window.clearTimeout(neuroManifestTimer)
    neuroManifestTimer = null
  }
  if (!neuroManifestDirty || !neuroManifest.value) return
  if (neuroManifestSaving) return
  const rev = neuroManifestRev
  const content = stringifyNeuroManifest()
  neuroManifestSaving = true
  neuroManifestError.value = ''
  try {
    await api('/manage/neuro_sama/ui/manifest', {
      method: 'PUT',
      body: JSON.stringify({ content }),
    })
    if (rev === neuroManifestRev) neuroManifestDirty = false
    // 概览里的 name/visible 也来自这份清单；保存后立即对齐，避免 5s 轮询先拿旧值。
    await refreshNeuroScenes()
    if (rev === neuroManifestRev) await refreshNeuroManifest(true)
  } catch (e) {
    neuroManifestError.value = describeError(e)
    showSnack(neuroManifestError.value)
  } finally {
    neuroManifestSaving = false
    if (neuroManifestDirty && rev !== neuroManifestRev) {
      neuroManifestTimer = window.setTimeout(() => {
        neuroManifestTimer = null
        void flushNeuroManifestSave()
      }, 1000)
    }
  }
}

function discardPendingNeuroManifest(): void {
  if (neuroManifestTimer !== null) {
    window.clearTimeout(neuroManifestTimer)
    neuroManifestTimer = null
  }
  neuroManifestDirty = false
}

async function refreshNeuroManifest(force = false): Promise<void> {
  if (!force && (neuroManifestDirty || neuroManifestSaving)) return
  try {
    const data = await api<NeuroManifest>('/manage/neuro_sama/ui/manifest')
    if (force || (!neuroManifestDirty && !neuroManifestSaving)) {
      neuroManifest.value = data
    }
  } catch {
    // 模块暂时不可达：内部层行退回"只给眼睛"（有未保存改动时保留本地缓存）。
    if (!neuroManifestDirty && !neuroManifestSaving) neuroManifest.value = null
  }
}

// ---------- 场景 CRUD ----------

interface SceneMenuEvent { clientX: number, clientY: number }

const sceneMenu = reactive<SceneMenuState>({ open: false, x: 0, y: 0, sceneId: '' })

function openSceneMenu(event: MouseEvent | SceneMenuEvent, sceneId: string): void {
  sceneMenu.open = true
  sceneMenu.x = event.clientX
  sceneMenu.y = event.clientY
  sceneMenu.sceneId = sceneId
}

const createOpen = ref(false)
const createForm = reactive({
  id: '',
  label: '',
  desc: '',
  icon: '',
  cloneCurrent: true,
})

const sceneIdPattern = /^[a-z0-9_-]+$/
const createIdError = computed(() => {
  const id = createForm.id.trim()
  if (!id) return ''
  if (!sceneIdPattern.test(id)) return '只能使用小写字母、数字、下划线、连字符'
  if (streamManifest.value?.scenes.some((s) => s.id === id)) return '场景 id 已存在'
  return ''
})
const canCreateScene = computed(() =>
  !!createForm.id.trim() && !createIdError.value,
)

function openCreateScene(): void {
  createForm.id = ''
  createForm.label = ''
  createForm.desc = ''
  createForm.icon = ''
  createForm.cloneCurrent = true
  createOpen.value = true
}

async function createScene(): Promise<void> {
  if (!(await ensureManifest()) || !streamManifest.value) return
  const id = createForm.id.trim()
  if (!id || createIdError.value) return
  const current = displayedScene.value
  const base = createForm.cloneCurrent && current
    ? {
        elements: cloneData(current.elements ?? []),
        ...(current.neuro ? { neuro: cloneData(current.neuro) } : {}),
      }
    : { elements: [], neuro: { x: 0, y: 0, w: 1 } }
  const scene: ManifestScene = {
    id,
    label: createForm.label.trim() || id,
    ...(createForm.desc.trim() ? { desc: createForm.desc.trim() } : {}),
    ...(createForm.icon.trim() ? { icon: createForm.icon.trim() } : {}),
    ...base,
  }
  streamManifest.value.scenes.push(scene)
  selectedSceneId.value = id
  selectedElement.value = null
  createOpen.value = false
  queueManifestSave()
}

const deleteOpen = ref(false)

function openDeleteScene(): void {
  if (!canDeleteScene.value) return
  deleteOpen.value = true
  deleteSceneLabel.value = selectedSceneRow.value?.label || selectedSceneId.value
}

const deleteSceneLabel = ref('')

async function deleteScene(): Promise<void> {
  const id = selectedSceneId.value
  if (!id || !streamManifest.value) return
  exitCanvasEdit()
  const idx = sceneIndex(id)
  if (idx < 0 || streamManifest.value.scenes.length <= 1) {
    deleteOpen.value = false
    return
  }
  streamManifest.value.scenes.splice(idx, 1)
  const fallback = streamManifest.value.scenes[Math.min(idx, streamManifest.value.scenes.length - 1)]
  selectedSceneId.value = fallback?.id ?? ''
  selectedElement.value = null
  deleteOpen.value = false
  queueManifestSave()
}

function moveScene(delta: number): void {
  const list = streamManifest.value?.scenes
  const idx = sceneIndex(selectedSceneId.value)
  if (!list || idx < 0) return
  const next = idx + delta
  if (next < 0 || next >= list.length) return
  const [scene] = list.splice(idx, 1)
  if (!scene) return
  list.splice(next, 0, scene)
  queueManifestSave()
}

// ---------- 重置清单（两个入口共用） ----------

const resetDialog = ref(false)

async function resetManifest(): Promise<void> {
  resetDialog.value = false
  exitCanvasEdit()
  discardPendingManifest()
  manifestError.value = ''
  try {
    await api('/manage/stream/ui/manifest/reset', { method: 'POST' })
    manifestRev += 1
    selectedElement.value = null
    await st.refreshScenes()
    await refreshStreamManifest(true)
    if (streamManifest.value?.scenes.some((s) => s.id === selectedSceneId.value)) {
      // 选中行还在就保留
    } else {
      selectedSceneId.value = displayedSceneId.value
    }
  } catch (e) {
    manifestError.value = describeError(e)
    showSnack(manifestError.value)
  }
}

// ---------- 场景属性对话框 ----------

const scenePropsOpen = ref(false)
const scenePropsId = ref('')
const timelineError = ref('')
const stateScriptError = ref('')
const sceneForm = reactive({
  label: '',
  desc: '',
  icon: '',
  duration: '',
  prompt_context: '',
  highlight_messages: true,
  timeline: '',
  state_script: '',
})

function scenePropsScene(): ManifestScene | null {
  return sceneById(scenePropsId.value)
}

async function openSceneProperties(sceneId: string): Promise<void> {
  sceneMenu.open = false
  if (!(await ensureManifest())) return
  const scene = sceneById(sceneId)
  if (!scene) return
  scenePropsId.value = sceneId
  sceneForm.label = scene.label ?? ''
  sceneForm.desc = scene.desc ?? ''
  sceneForm.icon = scene.icon ?? ''
  sceneForm.duration = scene.duration == null ? '' : String(scene.duration)
  sceneForm.prompt_context = scene.prompt_context ?? ''
  sceneForm.highlight_messages = scene.highlight_messages !== false
  sceneForm.timeline = scene.timeline ? JSON.stringify(scene.timeline, null, 2) : ''
  sceneForm.state_script = scene.state_script ? JSON.stringify(scene.state_script, null, 2) : ''
  timelineError.value = ''
  stateScriptError.value = ''
  scenePropsOpen.value = true
}

function applySceneBasics(): void {
  const scene = scenePropsScene()
  if (!scene) return
  const label = sceneForm.label.trim()
  if (label) scene.label = label
  else delete scene.label
  const desc = sceneForm.desc.trim()
  if (desc) scene.desc = desc
  else delete scene.desc
  const icon = sceneForm.icon.trim()
  if (icon) scene.icon = icon
  else delete scene.icon
  const duration = sceneForm.duration.trim()
  if (duration) scene.duration = Number(duration)
  else delete scene.duration
  const prompt = sceneForm.prompt_context
  if (prompt.trim()) scene.prompt_context = prompt
  else delete scene.prompt_context
  if (sceneForm.highlight_messages) scene.highlight_messages = true
  else scene.highlight_messages = false
  queueManifestSave()
}

function parseJsonField(
  text: string,
  label: string,
): { ok: true, value: unknown | null } | { ok: false, error: string } {
  if (!text.trim()) return { ok: true, value: null }
  try {
    const value = JSON.parse(text)
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return { ok: false, error: `${label} 必须是 JSON 对象` }
    }
    return { ok: true, value }
  } catch (e) {
    return { ok: false, error: `${label} JSON 解析失败：${(e as Error).message}` }
  }
}

function applySceneTimeline(): void {
  const scene = scenePropsScene()
  if (!scene) return
  const parsed = parseJsonField(sceneForm.timeline, 'timeline')
  if (!parsed.ok) {
    timelineError.value = parsed.error
    return
  }
  timelineError.value = ''
  if (parsed.value === null) delete scene.timeline
  else scene.timeline = parsed.value as Record<string, unknown>
  queueManifestSave()
}

function applySceneStateScript(): void {
  const scene = scenePropsScene()
  if (!scene) return
  const parsed = parseJsonField(sceneForm.state_script, 'state_script')
  if (!parsed.ok) {
    stateScriptError.value = parsed.error
    return
  }
  stateScriptError.value = ''
  if (parsed.value === null) delete scene.state_script
  else scene.state_script = parsed.value as Record<string, unknown>
  queueManifestSave()
}

async function closeSceneProps(): Promise<void> {
  scenePropsOpen.value = false
  await flushManifestSave()
}

// ---------- 元素设置对话框（stream 元素专用；neuro 内部层在 neuro 窗口面板编辑） ----------

const elementDialog = ref(false)
const elementTargetId = ref('')
const scriptError = ref('')
const propsError = ref('')
const elementFieldError = computed(() => scriptError.value || propsError.value)
const elementDialogError = computed(() => elementFieldError.value || manifestError.value)

const elementForm = reactive<ElementFormState>({
  placement: 'tl',
  x: '0',
  y: '0',
  w: '',
  h: '',
  scale: '',
  rotate: '',
  opacity: '',
  z: '0',
  visible: true,
  kind: 'component',
  color: '#000000',
  src: '',
  text: '',
  name: '',
  props: '',
  script: '',
})

function pctValue(value: number | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return ''
  const pct = value * 100
  return String(Math.round(pct * 10 ** digits) / 10 ** digits)
}

function parsePct(text: string): number | undefined {
  if (!text.trim()) return undefined
  const n = Number(text)
  return Number.isFinite(n) ? n / 100 : undefined
}

function placementOf(box: ManifestBox): string {
  const x = box.edge?.x === 'right'
  const y = box.edge?.y === 'bottom'
  if (x && y) return 'br'
  if (x) return 'tr'
  if (y) return 'bl'
  return 'tl'
}

function loadElementForm(el: ManifestElement): void {
  const box = el.box ?? { x: 0, y: 0 }
  elementForm.placement = placementOf(box)
  elementForm.x = pctValue(box.x ?? 0)
  elementForm.y = pctValue(box.y ?? 0)
  elementForm.w = pctValue(box.w)
  elementForm.h = pctValue(box.h)
  elementForm.scale = box.scale == null ? '' : String(box.scale)
  elementForm.rotate = box.rotate == null ? '' : String(box.rotate)
  elementForm.opacity = box.opacity == null ? '' : String(box.opacity)
  elementForm.z = String(el.z ?? 0)
  elementForm.visible = el.visible !== false

  const content = el.content ?? { kind: 'component' }
  elementForm.kind = String(content.kind ?? 'component')
  elementForm.color = String(content.color ?? '#000000')
  elementForm.src = String(content.src ?? '')
  elementForm.text = String(content.text ?? '')
  elementForm.name = String(content.name ?? '')
  elementForm.props = content.props
    ? JSON.stringify(content.props, null, 2)
    : ''
  elementForm.script = el.script ? JSON.stringify(el.script, null, 2) : ''
  scriptError.value = ''
  propsError.value = ''
}

function openElementSettings(elementId: string): void {
  const el = displayedScene.value?.elements?.find((e) => e.id === elementId)
  if (!el) return
  loadElementForm(el)
  elementTargetId.value = el.id
  elementDialog.value = true
}

function openSelectedSettings(): void {
  const selection = selectedElement.value
  if (!selection) return
  if (selection.kind === 'neuro-window') {
    openNeuroDialog()
    return
  }
  openElementSettings(selection.id)
}

function buildContent(el: ManifestElement): ManifestContent | null {
  const kind = elementForm.kind
  const base = (el.content ?? {}) as Record<string, unknown>
  if (kind === 'color') {
    return { kind, color: elementForm.color.trim() || '#000000' }
  }
  if (kind === 'image') {
    return {
      kind,
      src: elementForm.src.trim(),
      ...(base.fit ? { fit: base.fit } : {}),
    }
  }
  if (kind === 'video') {
    return {
      kind,
      src: elementForm.src.trim(),
      ...(base.muted ? { muted: true } : {}),
      ...(base.loop ? { loop: true } : {}),
      ...(base.autoplay ? { autoplay: true } : {}),
    }
  }
  if (kind === 'text') {
    return {
      kind,
      text: elementForm.text,
      ...(base.class ? { class: base.class } : {}),
    }
  }
  const parsed = parseJsonField(elementForm.props, 'props')
  if (!parsed.ok) {
    propsError.value = parsed.error
    return null
  }
  propsError.value = ''
  return {
    kind: 'component',
    name: elementForm.name.trim(),
    ...(parsed.value ? { props: parsed.value } : {}),
  }
}

/** 表单 → box（保持 stream 元素与 neuro 内部层同一套几何语义）。 */
function boxFromForm(base: ManifestBox | undefined): ManifestBox {
  const box: ManifestBox = { ...(base ?? { x: 0, y: 0 }) }
  box.x = parsePct(elementForm.x) ?? 0
  box.y = parsePct(elementForm.y) ?? 0
  const w = parsePct(elementForm.w)
  const h = parsePct(elementForm.h)
  if (w === undefined) delete box.w
  else box.w = w
  if (h === undefined) delete box.h
  else box.h = h

  const edge: { x?: 'left' | 'right', y?: 'top' | 'bottom' } = {}
  if (elementForm.placement === 'tr' || elementForm.placement === 'br') edge.x = 'right'
  if (elementForm.placement === 'bl' || elementForm.placement === 'br') edge.y = 'bottom'
  if (edge.x || edge.y) box.edge = edge
  else delete box.edge

  const scale = elementForm.scale.trim()
  if (scale) box.scale = Number(scale)
  else delete box.scale
  const rotate = elementForm.rotate.trim()
  if (rotate) box.rotate = Number(rotate)
  else delete box.rotate
  const opacity = elementForm.opacity.trim()
  if (opacity) box.opacity = Number(opacity)
  else delete box.opacity
  return box
}

/** 表单 → script；空 = 删键，JSON 不合法写进 scriptError 并保留原值。 */
function applyScriptField(target: { script?: Record<string, unknown> }): void {
  const scriptText = elementForm.script
  if (!scriptText.trim()) {
    delete target.script
    scriptError.value = ''
    return
  }
  const parsed = parseJsonField(scriptText, 'script')
  if (parsed.ok && parsed.value) {
    target.script = parsed.value as Record<string, unknown>
    scriptError.value = ''
  } else if (!parsed.ok) {
    scriptError.value = parsed.error
  }
}

function applyElementForm(): void {
  // neuro 窗口面板展开着某层时，同一份表单写 neuro manifest；否则写 stream 元素。
  if (neuroEditingLayer.value) {
    applyNeuroLayerForm(neuroEditingLayer.value)
    return
  }
  const el = selectedElementDef()
  if (!el) return

  el.box = boxFromForm(el.box)
  const z = Number(elementForm.z)
  el.z = Number.isFinite(z) ? z : 0
  el.visible = elementForm.visible

  if (elementForm.kind !== 'component') propsError.value = ''
  const content = buildContent(el)
  if (content) el.content = content

  applyScriptField(el)

  queueManifestSave()
}

/** neuro 内部层：同 id 在 normal/intro 里是同一家族，几何/z/脚本一起改；
 *  visible 是场景级状态（开场本来就和常态不同），只写 neuro 当前场景。 */
function applyNeuroLayerForm(layerId: string): void {
  const manifest = neuroManifest.value
  if (!manifest) return
  const currentSceneId = currentNeuroManifestScene.value?.id ?? ''
  const z = Number(elementForm.z)
  const zValue = Number.isFinite(z) ? z : 0
  let applied = 0
  for (const scene of manifest.scenes) {
    const el = scene.elements?.find((e) => e.id === layerId)
    if (!el) continue
    el.box = boxFromForm(el.box)
    el.z = zValue
    applyScriptField(el)
    if (scene.id === currentSceneId) el.visible = elementForm.visible
    applied += 1
  }
  if (!applied) return
  queueNeuroManifestSave()
}

async function closeElementDialog(): Promise<void> {
  elementDialog.value = false
  await flushManifestSave()
  await flushNeuroManifestSave()
}

// ---------- neuro 窗口对话框（窗口摆放 + 内部层内容） ----------

const neuroDialog = ref(false)
/** 手风琴当前展开层（单开模式；v-expansion-panels 的 model 值）。 */
const neuroExpandedLayer = ref<string | number | null>(null)
/** 表单当前服务的层（展开切换时先 flush 上一层的待发保存）。 */
const neuroEditingLayer = ref<string | null>(null)
const neuroForm = reactive({
  x: '0',
  y: '0',
  w: '100',
  z: '',
  hidden: false,
  hide: [] as string[],
})

const neuroHideItems = computed(() => {
  const seen = new Set<string>()
  const out: { title: string, value: string }[] = []
  for (const row of neuroLayerRows.value) {
    if (seen.has(row.layerId)) continue
    seen.add(row.layerId)
    out.push({ title: row.name, value: row.layerId })
  }
  for (const id of neuroForm.hide) {
    if (!seen.has(id)) out.push({ title: id, value: id })
  }
  return out
})

async function onLayerExpand(value: string | number | null): Promise<void> {
  const id = typeof value === 'string' ? value : null
  if (neuroEditingLayer.value && neuroEditingLayer.value !== id) {
    await flushNeuroManifestSave()
  }
  neuroEditingLayer.value = null
  if (!id) return
  const el = findNeuroLayerEl(id)
  if (!el) return
  loadElementForm(el)
  neuroEditingLayer.value = id
}

function openNeuroDialog(): void {
  const scene = displayedScene.value
  if (!scene) return
  neuroExpandedLayer.value = null
  neuroEditingLayer.value = null
  const neuro = scene.neuro
  neuroForm.x = pctValue(neuro?.x ?? 0)
  neuroForm.y = pctValue(neuro?.y ?? 0)
  neuroForm.w = pctValue(neuro?.w ?? 1)
  neuroForm.z = neuro?.z == null ? '' : String(neuro.z)
  neuroForm.hidden = Boolean(neuro?.hidden)
  neuroForm.hide = [...(neuro?.hide ?? [])]
  neuroDialog.value = true
}

function applyNeuroForm(): void {
  const scene = displayedScene.value
  if (!scene) return
  const neuro: ManifestNeuro = scene.neuro ?? { x: 0, y: 0, w: 1 }
  neuro.x = parsePct(neuroForm.x) ?? 0
  neuro.y = parsePct(neuroForm.y) ?? 0
  neuro.w = parsePct(neuroForm.w) ?? 1
  const z = neuroForm.z.trim()
  if (z) neuro.z = Number(z)
  else delete neuro.z
  if (neuroForm.hidden) neuro.hidden = true
  else delete neuro.hidden
  neuro.hide = [...neuroForm.hide]
  scene.neuro = neuro
  queueManifestSave()
}

async function closeNeuroDialog(): Promise<void> {
  neuroDialog.value = false
  neuroExpandedLayer.value = null
  neuroEditingLayer.value = null
  await flushManifestSave()
  await flushNeuroManifestSave()
}

// ---------- 源签名 / 隐藏集合 ----------

// 场景 / neuro 内部层切换 = 可用 source id 变了：hidden 只保留仍存在的 id。
// `neuro.window` 恒存在；neuro 层 id 随 neuro 当前场景变化。
const sourceSignature = computed(() =>
  [
    ...elementRows.value.map((el) => el.id),
    NEURO_WINDOW_ID,
    ...neuroLayerRows.value.map((row) => row.sourceId),
  ].join('|'),
)
watch(sourceSignature, () => {
  const ids = new Set(sourceSignature.value.split('|'))
  const next = hiddenIds.value.filter((id) => ids.has(id))
  if (next.length !== hiddenIds.value.length) {
    hiddenIds.value = next
  }
  // 切场景时合成器会重建元素层：无论 hidden 是否变化都重新下发
  sendVisibility()
})

const highlightPaused = computed(
  () => Boolean(st.status.value?.sections.loop.current?.highlight_paused),
)
const highlightSourceLabel = computed(() => {
  const source = st.status.value?.sections.loop.current?.highlight_source ?? ''
  const labels: Record<string, string> = {
    manual: '手动',
    'scene:stream': 'stream 场景',
    'scene:neuro': 'neuro 场景',
  }
  return labels[source] ?? '—'
})
const highlightControllable = computed(() => isRunning.value)

// ---------- 轮询 ----------

/** neuro 当前场景的内部层清单；模块不可达时清空（元素卡显示省略说明）。 */
async function refreshNeuroScenes(): Promise<void> {
  try {
    neuroScenes.value = await api<NeuroScenesResponse>('/manage/neuro_sama/ui/scenes')
  } catch {
    neuroScenes.value = null
  }
}

// stream 场景变化常伴随 neuro 换场景（intro→normal）：即时补一次内部层
watch(currentScene, () => {
  void refreshNeuroScenes()
  void refreshNeuroManifest()
})

async function doAction(kind: 'pause' | 'resume' | 'stop'): Promise<void> {
  await st.action(kind)
}

async function start(record: boolean): Promise<void> {
  if (record) await st.startRecord()
  else await st.action('start')
}

async function onHighlightToggle(value: boolean | null): Promise<void> {
  await st.setHighlightPaused(!Boolean(value))
}

// ---------- 设置对话框 ----------

const settingsOpen = ref(false)
const settingsBusy = ref(false)
const settingsError = ref('')
const formTitle = ref('')
const formCategory = ref('')
const formTags = ref<string[]>([])

async function openSettings(): Promise<void> {
  settingsError.value = ''
  settingsOpen.value = true
  if (isRunning.value) {
    const meta = st.status.value?.sections.metadata.current
    formTitle.value = meta?.title ?? ''
    formCategory.value = meta?.category ?? ''
    formTags.value = [...(meta?.tags ?? [])]
    return
  }
  settingsBusy.value = true
  try {
    const data = await api<{ config: Record<string, any> }>('/manage/config')
    const stream = (data.config?.stream ?? {}) as Record<string, any>
    formTitle.value = String(stream.default_title ?? '')
    formCategory.value = String(stream.default_category ?? '')
    formTags.value = Array.isArray(stream.default_tags) ? [...stream.default_tags] : []
  } catch (e) {
    settingsError.value = describeError(e)
  } finally {
    settingsBusy.value = false
  }
}

async function saveSettings(): Promise<void> {
  settingsError.value = ''
  settingsBusy.value = true
  try {
    if (isRunning.value) {
      const ok = await st.setMetadata(
        formTitle.value,
        formCategory.value,
        formTags.value,
      )
      if (!ok) {
        settingsError.value = st.error.value
        return
      }
    } else {
      // vedal 的全量语义（同 config.vue）：读整份 → 只改 stream.default_* → PUT 回去
      const data = await api<{ config: Record<string, any> }>('/manage/config')
      const cfg = data.config ?? {}
      cfg.stream = cfg.stream ?? {}
      cfg.stream.default_title = formTitle.value
      cfg.stream.default_category = formCategory.value
      cfg.stream.default_tags = [...formTags.value]
      await api('/manage/config', {
        method: 'PUT',
        body: JSON.stringify({ config: cfg }),
      })
    }
    settingsOpen.value = false
  } catch (e) {
    settingsError.value = describeError(e)
  } finally {
    settingsBusy.value = false
  }
}

// ---------- 错误 snackbar（409 等都走这里） ----------

const snackbar = ref(false)
const snackbarText = ref('')

function showSnack(message: string): void {
  snackbarText.value = message
  snackbar.value = true
}

watch(
  () => st.error.value,
  (message) => {
    if (message) showSnack(message)
  },
)

// ---------- 轮询 ----------

let statusTimer: number | null = null
let sceneTimer: number | null = null

function startPolling(): void {
  if (statusTimer !== null) return
  void st.refreshStatus()
  void st.refreshSessions()
  void st.refreshScenes()
  statusTimer = window.setInterval(() => {
    void st.refreshStatus()
    void st.refreshSessions()
  }, 3000)
  // 场景清单 / neuro 内部层不是高频数据：5s，别给后端加压力
  sceneTimer = window.setInterval(() => {
    void st.refreshScenes()
    void refreshNeuroScenes()
    void refreshNeuroManifest()
    void refreshStreamManifest()
  }, 5000)
}

function stopPolling(): void {
  if (statusTimer !== null) {
    window.clearInterval(statusTimer)
    statusTimer = null
  }
  if (sceneTimer !== null) {
    window.clearInterval(sceneTimer)
    sceneTimer = null
  }
}

watch(
  () => status.reachable.value,
  (reachable) => (reachable ? startPolling() : stopPolling()),
)

onMounted(() => {
  window.addEventListener('message', onCanvasMessage)
  void st.refreshScenes()
  void refreshNeuroScenes()
  void refreshNeuroManifest(true)
  void refreshStreamManifest(true)
  if (status.reachable.value) startPolling()
})

onUnmounted(() => {
  window.removeEventListener('message', onCanvasMessage)
  stopPolling()
  discardPendingManifest()
  discardPendingNeuroManifest()
})
</script>

<style scoped>
.studio-page {
  display: flex;
  flex-direction: column;
  /* fullBleed：App.vue 让 v-main 成为固定高度 flex 容器，页面直接填满剩余空间
     （不能再减顶栏/v-container padding——这条链由 .tw-main--fullbleed 闭合）。 */
  flex: 1 1 0;
  min-height: 0;
}

.studio-loading {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.studio-canvas {
  flex: 1 1 0;
  min-height: 0;
  position: relative;
  background: #000;
  display: flex;
}

.studio-canvas__iframe {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  border: 0;
  background: #000;
}

.studio-canvas__toolbar {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 1;
  display: flex;
  gap: 6px;
}

.studio-dock {
  flex: 0 0 auto;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 0 0;
}

.studio-card {
  flex: 1 1 210px;
  min-width: 200px;
  height: 240px;
  display: flex;
  flex-direction: column;
}

.studio-card__title {
  display: flex;
  align-items: center;
  font-size: 0.875rem;
  min-height: 40px;
  padding: 0 12px;
}

.studio-card__body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 12px 12px;
  font-size: 0.8125rem;
}

.studio-card__body--flush {
  padding-top: 2px;
}

.studio-card__actions {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 2px 6px 4px;
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}

.studio-scene-row,
.studio-element-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 30px;
  padding: 2px 6px;
  border-radius: 4px;
  cursor: pointer;
  user-select: none;
}

.studio-scene-row + .studio-scene-row,
.studio-element-row + .studio-element-row {
  margin-top: 2px;
}

.studio-scene-row:hover,
.studio-element-row:hover {
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.studio-scene-row--current {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
}

.studio-scene-row--current:hover {
  background: rgb(var(--v-theme-primary));
}

.studio-scene-row--selected,
.studio-element-row--selected {
  background: rgba(var(--v-theme-primary), 0.14);
}

.studio-element-row--readonly {
  opacity: 0.55;
}

.studio-color-block {
  width: 16px;
  height: 16px;
  border-radius: 3px;
  flex: 0 0 auto;
  border: 1px solid rgba(var(--v-border-color), 0.6);
}

.studio-menu-backdrop {
  position: fixed;
  inset: 0;
  z-index: 2000;
}

.studio-menu {
  position: fixed;
  z-index: 2001;
  min-width: 140px;
  background: rgb(var(--v-theme-surface));
  border-radius: 4px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.24);
}

.studio-json :deep(textarea) {
  font-family: 'Roboto Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
  tab-size: 2;
}
</style>
