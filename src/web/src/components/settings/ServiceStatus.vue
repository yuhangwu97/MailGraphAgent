<script setup lang="ts">
import type { ServiceStatus } from '@/api'

defineProps<{ services: ServiceStatus }>()

const items = [
  { key: 'ragflow' as const, name: 'RAGFlow', desc: '知识图谱引擎', port: '9380' },
  { key: 'redis' as const, name: 'Redis', desc: '进度缓存', port: '6379' },
  { key: 'mysql' as const, name: 'MySQL', desc: '元数据存储', port: '3306' },
  { key: 'minio' as const, name: 'MinIO', desc: '文件存储', port: '9000' },
]
</script>

<template>
  <div class="svc-grid">
    <div v-for="item in items" :key="item.key" class="svc-card">
      <div class="svc-dot" :class="{ ok: services[item.key] }"></div>
      <div class="svc-name">{{ item.name }}</div>
      <div class="svc-detail">{{ item.desc }} · :{{ item.port }}</div>
    </div>
  </div>
</template>

<style scoped>
.svc-grid {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem;
}

.svc-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r); padding: 1.25rem; text-align: center;
  box-shadow: var(--sh-sm); transition: all 0.2s;
}

.svc-card:hover { box-shadow: var(--sh-md); }

.svc-dot {
  width: 12px; height: 12px; border-radius: 50%;
  margin: 0 auto 0.5rem auto; background: #EF4444;
}

.svc-dot.ok { background: #10B981; }

.svc-name {
  font-size: 0.88rem; font-weight: 600; margin-bottom: 0.2rem; color: var(--t1);
}

.svc-detail { font-size: 0.7rem; color: var(--t4); }
</style>
