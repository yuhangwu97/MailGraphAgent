import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { accountsApi, statusApi, setActiveAccountId, type Account } from '@/api'

export const useAccountStore = defineStore('account', () => {
  const accounts = ref<Account[]>([])
  const activeId = ref<string | null>(null)
  const loading = ref(false)

  const active = computed(() => accounts.value.find(a => a.id === activeId.value) ?? null)

  // 让 API 客户端始终携带当前账户头。sync flush：activeId 一变立刻同步，
  // 早于任何监听 activeId 触发的重新拉取，避免用旧账户发请求。
  watch(activeId, (id) => setActiveAccountId(id), { immediate: true, flush: 'sync' })

  async function fetchAccounts() {
    loading.value = true
    try {
      const status = await statusApi.health()
      accounts.value = status.accounts
      if (!activeId.value || !accounts.value.find(a => a.id === activeId.value)) {
        activeId.value = status.active_account_id ?? accounts.value[0]?.id ?? null
      }
    } catch (e) {
      console.error('Failed to fetch accounts:', e)
    } finally {
      loading.value = false
    }
  }

  async function addAccount(data: {
    label: string; imap_server: string; imap_port: number
    email_user: string; email_pass: string; provider: string
  }) {
    const acct = await accountsApi.create(data)
    await fetchAccounts()
    activeId.value = acct.id
    return acct
  }

  async function deleteAccount(id: string) {
    await accountsApi.delete(id)
    if (activeId.value === id) activeId.value = null
    await fetchAccounts()
  }

  async function setDefault(id: string) {
    await accountsApi.setDefault(id)
    await fetchAccounts()
  }

  return { accounts, activeId, active, loading, fetchAccounts, addAccount, deleteAccount, setDefault }
})
