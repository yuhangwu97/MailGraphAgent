<script setup lang="ts">
import SvgIcon from '@/components/SvgIcon.vue'

defineProps<{
  name: string
  description: string
  people: { name: string }[]
  companies: { name: string }[]
}>()
</script>

<template>
  <div class="project-card">
    <div class="pc-header">
      <div class="pc-icon">
        <SvgIcon name="project" :size="16" />
      </div>
      <h3 class="pc-name">{{ name }}</h3>
    </div>

    <p class="pc-desc">{{ description || '图谱中暂无该项目的描述信息。' }}</p>

    <div class="pc-meta">
      <div class="pc-meta-item" :class="{ empty: !people.length }">
        <SvgIcon name="user" :size="13" />
        <span>{{ people.map(p => p.name).join('、') || '暂无关联人员' }}</span>
      </div>
      <div v-if="companies.length" class="pc-meta-item">
        <SvgIcon name="building" :size="13" />
        <span>{{ companies.map(c => c.name).join('、') }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.project-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 1.15rem 1.25rem;
  transition: box-shadow 0.15s, border-color 0.15s, transform 0.12s;
}

.project-card:hover {
  box-shadow: var(--sh-md);
  border-color: var(--t5);
  transform: translateY(-1px);
}

.pc-header {
  display: flex; align-items: center; gap: 0.55rem;
  margin-bottom: 0.5rem;
}

.pc-icon {
  width: 28px; height: 28px;
  border-radius: 7px;
  background: #FEF2F2;
  color: #9A3B2E;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}

.pc-name {
  font-size: 0.92rem; font-weight: 620; color: var(--t1);
  line-height: 1.3; margin: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

.pc-desc {
  font-size: 0.78rem; color: var(--t3); line-height: 1.55;
  margin-bottom: 0.7rem;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
  overflow: hidden;
}

.pc-meta {
  display: flex; flex-direction: column; gap: 0.3rem;
  padding-top: 0.55rem;
  border-top: 1px solid var(--border-light);
}

.pc-meta-item {
  display: flex; align-items: center; gap: 5px;
  font-size: 0.73rem; color: var(--t3);
}

.pc-meta-item.empty {
  color: var(--t4);
}
</style>
