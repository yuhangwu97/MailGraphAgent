<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'
import { useStatusStore } from '@/stores/status'
import { onMounted } from 'vue'
import SvgIcon from '@/components/SvgIcon.vue'

const router = useRouter()
const route = useRoute()
const statusStore = useStatusStore()

onMounted(() => {
  statusStore.refresh()
})

const navItems = [
  { key: 'chat', icon: 'chat' as const, label: 'AI 对话' },
  { key: 'dashboard', icon: 'dashboard' as const, label: '项目看板' },
  { key: 'workbench', icon: 'inbox' as const, label: '邮件工作台' },
  { key: 'graph', icon: 'graph' as const, label: '关系图谱' },
  { key: 'settings', icon: 'settings' as const, label: '系统设置' },
]

function currentPage(): string {
  const name = route.name as string
  return name || 'chat'
}

function navigate(key: string) {
  router.push({ name: key })
}
</script>

<template>
  <aside class="sidebar">
    <!-- Logo -->
    <div class="logo-section">
      <div class="logo-mark">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="5" r="2"/>
          <circle cx="5" cy="19" r="2"/>
          <circle cx="19" cy="19" r="2"/>
          <line x1="12" y1="7" x2="5" y2="17"/>
          <line x1="12" y1="7" x2="19" y2="17"/>
          <line x1="6.5" y1="17.5" x2="17.5" y2="17.5"/>
        </svg>
      </div>
      <div>
        <div class="logo-title">MailGraph</div>
        <div class="logo-sub">邮件关系分析</div>
      </div>
    </div>

    <!-- Navigation -->
    <nav>
      <button
        v-for="item in navItems"
        :key="item.key"
        :class="['nav-btn', { active: currentPage() === item.key }]"
        @click="navigate(item.key)"
      >
        <SvgIcon :name="item.icon" :size="17" class="nav-icon" />
        <span>{{ item.label }}</span>
      </button>
    </nav>

    <div class="sidebar-footer">
      <!-- Service status dots -->
      <div class="status-row">
        <span
          class="status-dot"
          :class="{ ok: statusStore.services.redis }"
          title="Redis"
        >RDS</span>
        <span class="status-dot-sep">·</span>
        <span
          class="status-dot"
          :class="{ ok: statusStore.services.neo4j }"
          title="Neo4j"
        >NEO</span>
        <span class="status-dot-sep">·</span>
        <span
          class="status-dot"
          :class="{ ok: statusStore.services.milvus }"
          title="Milvus"
        >MLV</span>
      </div>
      <div class="version">v3.0</div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  position: fixed; top: 0; left: 0; bottom: 0;
  width: var(--sidebar-w);
  background: var(--side);
  display: flex; flex-direction: column;
  padding: 1.25rem 0.75rem 0.75rem;
  z-index: 100;
}

.logo-section {
  display: flex; align-items: center; gap: 10px;
  padding: 0 0.5rem 1rem 0.5rem;
  margin-bottom: 0.5rem;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}

.logo-mark {
  width: 34px; height: 34px; border-radius: 8px;
  background: linear-gradient(135deg, #1F6F5C 0%, #2E9E6E 100%);
  display: flex; align-items: center; justify-content: center;
  color: #fff; flex-shrink: 0;
}

.logo-title {
  font-size: 0.9rem; font-weight: 650; color: #F1F5F9;
  letter-spacing: -0.2px; line-height: 1.2;
}

.logo-sub {
  font-size: 0.62rem; color: #64748B; font-weight: 450;
  letter-spacing: 0.2px;
}

nav {
  display: flex; flex-direction: column; gap: 1px;
  flex: 1;
}

.nav-btn {
  display: flex; align-items: center; gap: 9px;
  padding: 0.5rem 0.7rem; border-radius: 7px;
  border: none; background: transparent;
  color: #94A3B8; font-size: 0.82rem; font-weight: 480;
  cursor: pointer; transition: all 0.12s ease; text-align: left;
  width: 100%; font-family: inherit;
}

.nav-btn:hover {
  background: rgba(255,255,255,0.06);
  color: #CBD5E1;
}

.nav-btn.active {
  background: rgba(31,111,92,0.18);
  color: #A3D9CB;
  font-weight: 550;
}

.nav-icon {
  flex-shrink: 0;
  opacity: 0.7;
}

.nav-btn.active .nav-icon {
  opacity: 1;
}

.sidebar-footer {
  padding: 0.75rem 0.5rem 0;
  border-top: 1px solid rgba(255,255,255,0.06);
}

.status-row {
  display: flex; align-items: center; gap: 2px;
  font-size: 0.58rem; font-weight: 500;
}

.status-dot {
  color: #EF4444;
  letter-spacing: 0.3px;
}

.status-dot.ok {
  color: #10B981;
}

.status-dot-sep {
  color: #334155;
  margin: 0 1px;
}

.version {
  font-size: 0.6rem; color: #475569;
  margin-top: 0.35rem;
}
</style>
