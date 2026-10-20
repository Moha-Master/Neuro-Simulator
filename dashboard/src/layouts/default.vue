<template>
  <!-- App.vue 已经提供 v-app / v-app-bar / v-main；这里不再重复套一层
       v-layout + v-main（否则 DOM 里会出现两个 <main>，且内层高度不跟随外层）。 -->
  <router-view />
</template>

<script lang="ts" setup>
// Default layout
</script>
