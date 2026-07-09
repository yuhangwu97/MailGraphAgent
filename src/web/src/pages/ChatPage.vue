<script setup lang="ts">
import { onMounted, ref, nextTick, watch } from 'vue'
import { useChatStore } from '@/stores/chat'
import ChatMessage from '@/components/chat/ChatMessage.vue'
import ChatInput from '@/components/chat/ChatInput.vue'
import ChatHero from '@/components/chat/ChatHero.vue'
import BrainFab from '@/components/layout/BrainFab.vue'

const chatStore = useChatStore()
const messagesEl = ref<HTMLElement | null>(null)
const chatInputRef = ref<InstanceType<typeof ChatInput> | null>(null)

onMounted(async () => {
  await chatStore.fetchSessions()
  await chatStore.ensureSession()
  if (chatStore.activeSessionId) {
    await chatStore.loadMessages(chatStore.activeSessionId)
  }
  await chatStore.loadMemory()
})

watch(() => chatStore.messages.length, async () => {
  await nextTick()
  messagesEl.value?.scrollTo({ top: messagesEl.value.scrollHeight, behavior: 'smooth' })
})

watch(() => chatStore.streamAnswer, async () => {
  await nextTick()
  messagesEl.value?.scrollTo({ top: messagesEl.value.scrollHeight, behavior: 'smooth' })
})

function handleSuggest(text: string) {
  chatInputRef.value?.setText(text)
}

async function handleSend(question: string) {
  await chatStore.sendMessage(question)
}
</script>

<template>
  <div class="chat-page">
    <!-- Session toolbar (top-right) -->
    <div class="chat-toolbar" v-if="chatStore.sessions.length > 0">
      <select
        class="session-select"
        :value="chatStore.activeSessionId"
        @change="chatStore.switchSession(($event.target as HTMLSelectElement).value)"
      >
        <option
          v-for="s in chatStore.sessions"
          :key="s.id"
          :value="s.id"
        >{{ s.title }}</option>
      </select>
      <button class="btn btn-sm btn-secondary" @click="chatStore.createSession()" title="新建对话">
        ＋
      </button>
    </div>

    <!-- Messages -->
    <div class="messages-area" ref="messagesEl">
      <ChatHero v-if="chatStore.messages.length === 0 && !chatStore.streaming" @suggest="handleSuggest" />
      <ChatMessage
        v-for="msg in chatStore.messages"
        :key="msg.id"
        :message="msg"
        :result="msg.result ?? null"
      />
      <!-- Streaming message -->
      <ChatMessage
        v-if="chatStore.streaming"
        :message="{ id: 'stream', role: 'assistant', content: chatStore.streamAnswer, created_at: Date.now() / 1000 }"
        :streaming="true"
        :result="chatStore.streamResult"
        :progress="chatStore.streamProgress"
      />
    </div>

    <!-- Input -->
    <ChatInput ref="chatInputRef" :disabled="chatStore.streaming" @send="handleSend" />

    <!-- Floating brain -->
    <BrainFab />
  </div>
</template>

<style scoped>
.chat-page {
  display: flex; flex-direction: column; height: calc(100vh - 3rem);
  max-width: 820px; margin: 0 auto;
}

.chat-toolbar {
  display: flex; align-items: center; justify-content: flex-end;
  gap: 0.4rem; padding: 0.35rem 0; margin-bottom: 0.25rem;
}

.session-select {
  width: auto; min-width: 140px; max-width: 220px;
  padding: 0.3rem 0.5rem; font-size: 0.8rem;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r-sm); color: var(--t2);
}

.messages-area {
  flex: 1; overflow-y: auto; padding: 0.5rem 0;
}

.chat-message {
  max-width: 100%; margin: 0.35rem 0; padding: 0.35rem 0;
  background: transparent; border: none; box-shadow: none;
}
</style>
