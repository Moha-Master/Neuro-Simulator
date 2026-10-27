<template>
  <svg
    class="twitch-icon"
    :viewBox="def.viewBox"
    :width="size"
    :height="size"
    aria-hidden="true"
    focusable="false"
    v-html="def.html"
  />
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import { TWITCH_ICONS, type TwitchIconName } from './icons'

const props = withDefaults(
  defineProps<{
    name: TwitchIconName
    size?: number | string
  }>(),
  { size: 20 },
)

const def = computed(() => TWITCH_ICONS[props.name])
</script>

<style scoped>
.twitch-icon {
  display: block;
  flex-shrink: 0;
  fill: currentColor;
}
</style>
