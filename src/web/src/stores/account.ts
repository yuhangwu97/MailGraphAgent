import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { accountsApi, statusApi, type Account } from '@/api'

export const useAccountStore = defineStore('account', () => {
  const accounts = ref<Account[]>([])
  const activeId = ref<string | null>(null)
  const loading = ref(false)

  const active = computed(() => accounts.value.find(a => a.id === activeId.value) ?? null)

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

  return { accounts, activeId, active, loading, fetchAccounts, addAccount, deleteAccount }
})
