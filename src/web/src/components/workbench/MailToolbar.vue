<script setup lang="ts">
import { ref } from 'vue'

defineProps<{
  fetching: boolean
  ingesting: boolean
  pendingCount: number
}>()

const emit = defineEmits<{
  fetch: [folder: string, limit: number]
  ingest: []
}>()

const folder = ref('INBOX')
const limit = ref(20)
</script>

<template>
  <div class="toolbar">
    <div class="tool-group">
      <input v-model.number="limit" type="number" min="1" max="200" class="num-input" />
      <select v-model="folder">
        <option value="INBOX">INBOX</option>
        <option value="[Gmail]/Sent Mail">[Gmail]/Sent Mail</option>
      </select>
    </div>
    <button class="btn btn-primary" :disabled="fetching" @click="emit('fetch', folder, limit)">
      📥 {{ fetching ? '拉取中…' : '拉取' }}
    </button>
    <button
      class="btn btn-primary"
      :disabled="ingesting || pendingCount === 0"
      @click="emit('ingest')"
    >
      ⚡ {{ ingesting ? '导入中…' : `导入图谱 (${pendingCount})` }}
    </button>
  </div>
</template>

<style scoped>
.toolbar { display: flex; gap: 0.75rem; align-items: center; }

.tool-group {
  display: flex; gap: 0.5rem; align-items: center;
}

.num-input { width: 80px; }
</style>
