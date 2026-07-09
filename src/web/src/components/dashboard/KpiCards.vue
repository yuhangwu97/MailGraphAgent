<script setup lang="ts">
import SvgIcon from '@/components/SvgIcon.vue'

defineProps<{
  totalMails: number
  doneMails: number
  graphNodes: number
  projects: number
  contacts: number
}>()

const cards = [
  { icon: 'mail' as const, key: 'totalMails' as const, label: '邮件总数', color: 'var(--t2)' },
  { icon: 'check' as const, key: 'doneMails' as const, label: '已处理', color: 'var(--green)' },
  { icon: 'graph' as const, key: 'graphNodes' as const, label: '图谱节点', color: '#7A756E' },
  { icon: 'project' as const, key: 'projects' as const, label: '项目数', color: 'var(--red)' },
  { icon: 'user' as const, key: 'contacts' as const, label: '联系人', color: '#C08A3E' },
]
</script>

<template>
  <div class="kpi-row">
    <div v-for="card in cards" :key="card.key" class="kpi-tile">
      <div class="kpi-icon-wrap" :style="{ color: card.color }">
        <SvgIcon :name="card.icon" :size="20" />
      </div>
      <div class="kpi-info">
        <div class="kpi-value">{{ $props[card.key].toLocaleString() }}</div>
        <div class="kpi-label">{{ card.label }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.kpi-row {
  display: flex; gap: 1rem; flex-wrap: wrap;
  margin-bottom: 1.75rem;
}

.kpi-tile {
  flex: 1; min-width: 170px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 1rem 1.15rem;
  display: flex; align-items: center; gap: 0.85rem;
  transition: box-shadow 0.15s, border-color 0.15s;
}

.kpi-tile:hover {
  box-shadow: var(--sh);
  border-color: var(--t5);
}

.kpi-icon-wrap {
  width: 38px; height: 38px;
  border-radius: 9px;
  display: flex; align-items: center; justify-content: center;
  background: var(--surface-2);
  flex-shrink: 0;
}

.kpi-value {
  font-size: 1.55rem; font-weight: 680; color: var(--t1);
  line-height: 1.15; letter-spacing: -0.4px;
}

.kpi-label {
  font-size: 0.7rem; color: var(--t4); font-weight: 480;
  margin-top: 2px;
}
</style>
