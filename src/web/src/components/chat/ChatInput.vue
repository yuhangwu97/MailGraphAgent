<script setup lang="ts">
import { ref } from 'vue'

defineProps<{ disabled?: boolean }>()
const emit = defineEmits<{ send: [text: string] }>()

const text = ref('')

function setText(val: string) {
  text.value = val
}

defineExpose({ setText })

function submit() {
  const trimmed = text.value.trim()
  if (!trimmed) return
  emit('send', trimmed)
  text.value = ''
}
</script>

<template>
  <div class="chat-input-wrap">
    <form @submit.prevent="submit" class="chat-input-form">
      <input
        v-model="text"
        type="text"
        class="chat-input"
        placeholder="给 MailGraph 发消息…问客户、项目、人物关系"
        :disabled="disabled"
        @keydown.enter="submit"
      />
      <button type="submit" class="send-btn" :disabled="disabled || !text.trim()">
        ↑
      </button>
    </form>
  </div>
</template>

<style scoped>
.chat-input-wrap {
  padding: 0.75rem 0 0.5rem 0;
}

.chat-input-form {
  display: flex; align-items: center; gap: 0.5rem;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 16px; padding: 0.3rem 0.5rem 0.3rem 1rem;
  box-shadow: var(--sh-md);
}

.chat-input {
  flex: 1; border: none; background: transparent;
  font-size: 0.92rem; padding: 0.5rem 0; outline: none;
  font-family: inherit;
}

.send-btn {
  width: 34px; height: 34px; border-radius: 50%;
  border: none; background: var(--p); color: #fff;
  font-size: 1.1rem; cursor: pointer; display: flex;
  align-items: center; justify-content: center;
  flex-shrink: 0; transition: all 0.15s;
}

.send-btn:hover { background: var(--p-hover); }
.send-btn:disabled { background: var(--t5); cursor: not-allowed; }
</style>
