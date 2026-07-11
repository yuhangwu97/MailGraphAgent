<script setup lang="ts">
import { onMounted } from 'vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import { useAccountStore } from '@/stores/account'

const accountStore = useAccountStore()

onMounted(() => {
  accountStore.fetchAccounts()
})
</script>

<template>
  <AppSidebar />
  <main class="main-content">
    <router-view />
  </main>
</template>

<style scoped>
.main-content {
  flex: 1;
  padding: 1.5rem 2rem 2rem 2rem;
  min-width: 0;
}
</style>
