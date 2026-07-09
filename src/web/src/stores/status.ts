import { defineStore } from 'pinia'
import { ref } from 'vue'
import { statusApi, type ServiceStatus } from '@/api'

export const useStatusStore = defineStore('status', () => {
  const services = ref<ServiceStatus>({ ragflow: false, redis: false, mysql: true, minio: true })
  const activeAccountId = ref<string | null>(null)
  const loading = ref(false)

  async function refresh() {
    loading.value = true
    try {
      const status = await statusApi.health()
      services.value = status.services
      activeAccountId.value = status.active_account_id
    } catch {
      services.value = { ragflow: false, redis: false, mysql: true, minio: true }
    } finally {
      loading.value = false
    }
  }

  return { services, activeAccountId, loading, refresh }
})
