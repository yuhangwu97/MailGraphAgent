import { defineStore } from 'pinia'
import { ref } from 'vue'
import { statusApi, type ServiceStatus } from '@/api'

export const useStatusStore = defineStore('status', () => {
  const services = ref<ServiceStatus>({ redis: false, neo4j: false, milvus: false })
  const activeAccountId = ref<string | null>(null)
  const loading = ref(false)

  async function refresh() {
    loading.value = true
    try {
      const status = await statusApi.health()
      services.value = status.services
      activeAccountId.value = status.active_account_id
    } catch {
      services.value = { redis: false, neo4j: false, milvus: false }
    } finally {
      loading.value = false
    }
  }

  return { services, activeAccountId, loading, refresh }
})
