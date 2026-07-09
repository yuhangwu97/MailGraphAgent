<script setup lang="ts">
import { ref } from 'vue'
import { useChatStore } from '@/stores/chat'

const chatStore = useChatStore()
const showModal = ref(false)

function open() { showModal.value = true }
function close() { showModal.value = false }
</script>

<template>
  <!-- Floating brain button -->
  <button
    v-if="chatStore.memory?.summary || chatStore.memory?.last_topics?.length"
    class="brain-fab"
    :class="{ 'has-memory': true }"
    title="Agent Memory"
    @click="open"
  >
    🧠
  </button>

  <!-- Modal -->
  <Teleport to="body">
    <div v-if="showModal" class="modal-overlay" @click.self="close">
      <div class="modal-card">
        <div class="modal-header">
          <h3>🧠 Agent Memory</h3>
          <button class="modal-close" @click="close">✕</button>
        </div>
        <div class="modal-body">
          <div v-if="chatStore.memory?.preferences?.length" class="mem-section">
            <div class="mem-label">用户偏好</div>
            <p>{{ chatStore.memory.preferences.join('；') }}</p>
          </div>
          <div v-if="chatStore.memory?.pinned_context?.length" class="mem-section">
            <div class="mem-label">固定上下文</div>
            <p>{{ chatStore.memory.pinned_context.join('；') }}</p>
          </div>
          <div v-if="chatStore.memory?.last_topics?.length" class="mem-section">
            <div class="mem-label">最近关注</div>
            <p>{{ chatStore.memory.last_topics.join('；') }}</p>
          </div>
          <div v-if="chatStore.memory?.summary" class="mem-section">
            <div class="mem-label">会话摘要</div>
            <p>{{ chatStore.memory.summary }}</p>
          </div>
          <div v-if="!hasAnyMemory" class="text-muted">
            暂无记忆。随着对话进行，Agent 会记录关键信息。
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script lang="ts">
const hasAnyMemory = false
</script>

<style scoped>
.brain-fab {
  position: fixed; bottom: 108px; right: 36px; z-index: 999;
  width: 44px; height: 44px; border-radius: 50%;
  background: var(--surface); border: 1.5px solid var(--border);
  box-shadow: var(--sh-md); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  font-size: 1.2rem; transition: all 0.2s ease;
  color: var(--t2);
}

.brain-fab:hover {
  transform: scale(1.08); box-shadow: var(--sh-lg);
  border-color: var(--p); background: var(--p-light);
}

.brain-fab:active { transform: scale(0.95); }

.brain-fab.has-memory {
  background: var(--p-light); border-color: var(--p);
  animation: brain-pulse 2s ease-in-out infinite;
}

@keyframes brain-pulse {
  0%, 100% { box-shadow: var(--sh-md); }
  50% { box-shadow: 0 0 0 6px rgba(31,111,92,0.08), var(--sh-md); }
}

/* Modal */
.modal-overlay {
  position: fixed; inset: 0; z-index: 10000;
  background: rgba(0,0,0,0.3);
  display: flex; align-items: center; justify-content: center;
  backdrop-filter: blur(2px);
}

.modal-card {
  background: var(--surface); border-radius: var(--r-lg);
  box-shadow: var(--sh-lg); width: 480px; max-width: 90vw;
  max-height: 80vh; overflow-y: auto;
}

.modal-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 1.25rem 1.5rem; border-bottom: 1px solid var(--border);
}

.modal-header h3 { margin: 0; font-size: 1.05rem; }

.modal-close {
  background: none; border: none; font-size: 1.1rem; cursor: pointer;
  color: var(--t4); padding: 0.25rem; border-radius: var(--r-sm);
}

.modal-close:hover { color: var(--t1); background: var(--surface-2); }

.modal-body { padding: 1.25rem 1.5rem; }

.mem-section { margin-bottom: 1rem; }

.mem-label {
  font-size: 0.7rem; font-weight: 600; color: var(--t4);
  text-transform: uppercase; letter-spacing: 0.3px; margin-bottom: 0.25rem;
}

.mem-section p { font-size: 0.85rem; color: var(--t2); line-height: 1.6; }
</style>
