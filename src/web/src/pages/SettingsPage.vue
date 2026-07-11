<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { statusApi, type ServiceStatus } from '@/api'
import ServiceStatusView from '@/components/settings/ServiceStatus.vue'

const services = ref<ServiceStatus>({ redis: false, neo4j: false, milvus: false })
const tokens = ref({ prompt_tokens: 0, completion_tokens: 0 })
const logLines = ref(50)
const activeLogService = ref('neo4j')
const logContent = ref('')
const logLoading = ref(false)

const logServices = [
  { key: 'neo4j', label: 'Neo4j', container: 'mailgraph-neo4j' },
  { key: 'milvus', label: 'Milvus', container: 'mailgraph-milvus' },
  { key: 'redis', label: 'Redis', container: 'mailgraph-redis' },
]

async function refreshStatus() {
  try {
    const s = await statusApi.health()
    services.value = s.services
  } catch (e) { console.error(e) }
  try {
    tokens.value = await statusApi.tokens()
  } catch (e) { console.error(e) }
}

async function loadLogs() {
  logLoading.value = true
  try {
    const res = await statusApi.logs(activeLogService.value, logLines.value)
    logContent.value = res.log
  } catch (e: any) {
    logContent.value = '获取失败: ' + (e.message || e)
  } finally {
    logLoading.value = false
  }
}

onMounted(() => {
  refreshStatus()
  loadLogs()
})

// 切换服务标签 / 行数时自动刷新，无需手动点刷新
watch([activeLogService, logLines], loadLogs)
</script>

<template>
  <div>
    <h2>⚙️ 系统设置</h2>

    <!-- Service Status -->
    <h3>服务状态</h3>
    <ServiceStatusView :services="services" />

    <hr />

    <!-- API Usage -->
    <h3>API 用量</h3>
    <div class="usage-row">
      <div class="usage-tile">
        <div class="usage-value">{{ tokens.prompt_tokens.toLocaleString() }}</div>
        <div class="usage-label">输入 Token</div>
      </div>
      <div class="usage-tile">
        <div class="usage-value">{{ tokens.completion_tokens.toLocaleString() }}</div>
        <div class="usage-label">输出 Token</div>
      </div>
      <div class="usage-tile">
        <div class="usage-value">{{ (tokens.prompt_tokens + tokens.completion_tokens).toLocaleString() }}</div>
        <div class="usage-label">合计</div>
      </div>
    </div>

    <hr />

    <!-- Logs -->
    <h3>服务日志</h3>
    <div class="log-toolbar">
      <div class="log-tabs">
        <button
          v-for="svc in logServices" :key="svc.key"
          :class="['btn btn-sm', activeLogService === svc.key ? 'btn-primary' : 'btn-secondary']"
          @click="activeLogService = svc.key"
        >{{ svc.label }}</button>
      </div>
      <div class="log-controls">
        <select v-model.number="logLines">
          <option :value="20">20 行</option>
          <option :value="50">50 行</option>
          <option :value="100">100 行</option>
        </select>
        <button class="btn btn-sm btn-primary" :disabled="logLoading" @click="loadLogs">
          🔄 刷新
        </button>
      </div>
    </div>
    <pre v-if="logContent" class="log-viewer">{{ logContent }}</pre>
    <p v-else class="text-muted">点击刷新查看日志</p>
  </div>
</template>

<style scoped>
.usage-row {
  display: flex; gap: 1rem; flex-wrap: wrap;
  margin-bottom: 0.5rem;
}

.usage-tile {
  flex: 1; min-width: 150px;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r); padding: 1rem 1.15rem;
}

.usage-value {
  font-size: 1.5rem; font-weight: 680; color: var(--t1);
  line-height: 1.15; letter-spacing: -0.4px;
}

.usage-label {
  font-size: 0.7rem; color: var(--t4); font-weight: 480;
  margin-top: 3px;
}

.log-toolbar {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 0.5rem;
}

.log-tabs { display: flex; gap: 0.25rem; }

.log-controls {
  display: flex; align-items: center; gap: 0.5rem;
}

.log-controls select { width: auto; }

.log-viewer {
  background: #1E293B; color: #E2E8F0;
  font-family: 'SF Mono', 'JetBrains Mono', monospace;
  font-size: 0.78rem; padding: 1rem; border-radius: var(--r-sm);
  border: 1px solid var(--border); max-height: 500px; overflow-y: auto;
  white-space: pre-wrap; word-break: break-all; line-height: 1.5;
}
</style>
