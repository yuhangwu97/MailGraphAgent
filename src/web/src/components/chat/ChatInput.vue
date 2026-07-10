<script setup lang="ts">
import { ref, nextTick } from 'vue'

const props = defineProps<{ disabled?: boolean }>()
const emit = defineEmits<{ send: [text: string] }>()

const text = ref('')
const inputEl = ref<HTMLInputElement | null>(null)

function setText(val: string) {
  text.value = val
  nextTick(() => inputEl.value?.focus())
}

defineExpose({ setText })

function submit() {
  const trimmed = text.value.trim()
  if (!trimmed || props.disabled) return
  emit('send', trimmed)
  text.value = ''
  nextTick(() => inputEl.value?.focus())
}
</script>

<template>
  <div class="chat-input-wrap">
    <form @submit.prevent="submit" class="chat-input-form">
      <input
        ref="inputEl"
        v-model="text"
        type="text"
        class="chat-input"
        :placeholder="disabled ? 'AI 正在回复…' : '输入你的问题…'"
        :disabled="disabled"
        @keydown.enter="submit"
        autocomplete="off"
      />
      <button
        type="submit"
        class="send-btn"
        :class="{ active: text.trim() && !disabled }"
        :disabled="disabled || !text.trim()"
        title="发送"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <line x1="12" y1="19" x2="12" y2="5"/>
          <polyline points="5 12 12 5 19 12"/>
        </svg>
      </button>
    </form>
  </div>
</template>

<style scoped>
.chat-input-wrap {
  padding: 0.75rem 0 0.5rem;
}

.chat-input-form {
  display: flex; align-items: center; gap: 0.5rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 0.25rem 0.4rem 0.25rem 1.1rem;
  box-shadow: var(--sh-sm);
  transition: border-color var(--dur-fast), box-shadow var(--dur-fast);
}

.chat-input-form:focus-within {
  border-color: var(--p);
  box-shadow: var(--sh-md), 0 0 0 3px var(--p-ring);
}

.chat-input {
  flex: 1; border: none; background: transparent;
  font-size: 0.94rem; padding: 0.55rem 0; outline: none;
  font-family: inherit; color: var(--t1);
}
.chat-input::placeholder { color: var(--t4); }
.chat-input:disabled { cursor: not-allowed; opacity: 0.6; }

.send-btn {
  width: 36px; height: 36px; border-radius: 50%;
  border: none; background: var(--t5); color: #fff;
  cursor: pointer; display: flex; align-items: center;
  justify-content: center; flex-shrink: 0;
  transition: all 0.2s var(--ease);
}
.send-btn.active {
  background: var(--p);
  box-shadow: 0 2px 8px rgba(31, 111, 92, 0.3);
}
.send-btn.active:hover {
  background: var(--p-hover);
  transform: scale(1.05);
}
.send-btn:disabled {
  background: var(--t5); cursor: not-allowed;
  box-shadow: none; transform: none;
}
</style>
