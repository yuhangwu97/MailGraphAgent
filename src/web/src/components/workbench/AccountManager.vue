<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAccountStore } from '@/stores/account'

const accountStore = useAccountStore()
const showForm = ref(false)

const providerPresets: Record<string, [string, number]> = {
  'Gmail': ['imap.gmail.com', 993],
  'QQ 邮箱': ['imap.qq.com', 993],
  '阿里企业邮箱': ['imap.mxhichina.com', 993],
  '自定义': ['', 993],
}

const provider = ref('Gmail')
const label = ref('')
const server = ref(providerPresets[provider.value][0])
const port = ref(providerPresets[provider.value][1])
const emailUser = ref('')
const emailPass = ref('')

function onProviderChange() {
  const [s, p] = providerPresets[provider.value]
  server.value = s; port.value = p
}

async function submit() {
  if (!emailUser.value || !emailPass.value || !server.value) return
  await accountStore.addAccount({
    label: label.value,
    imap_server: server.value,
    imap_port: port.value,
    email_user: emailUser.value,
    email_pass: emailPass.value,
    provider: provider.value,
  })
  emailUser.value = ''; emailPass.value = ''; label.value = ''
  showForm.value = false
}

onMounted(async () => {
  if (!accountStore.accounts.length) await accountStore.fetchAccounts()
  if (!accountStore.accounts.length) showForm.value = true
})
</script>

<template>
  <div class="account-bar">
    <!-- Selector + toggle -->
    <div class="bar-row">
      <div class="bar-left">
        <span class="bar-label">处理邮箱</span>
        <select
          v-if="accountStore.accounts.length"
          :value="accountStore.activeId"
          @change="accountStore.activeId = ($event.target as HTMLSelectElement).value"
          class="bar-select"
        >
          <option v-for="a in accountStore.accounts" :key="a.id" :value="a.id">
            {{ a.label || a.email_user }} · {{ a.email_user }}
          </option>
        </select>
        <span v-else class="text-muted">未配置</span>
      </div>
      <div class="bar-right">
        <span v-if="accountStore.accounts.length" class="account-count">
          {{ accountStore.accounts.length }} 个账号
        </span>
        <button class="btn btn-secondary btn-sm" @click="showForm = !showForm">
          ⚙️ {{ showForm ? '收起' : '管理' }}
        </button>
      </div>
    </div>

    <!-- Add form -->
    <div v-if="showForm" class="add-form">
      <h5>添加邮箱账号</h5>
      <div class="form-grid">
        <label>
          <span class="form-label">服务商</span>
          <select v-model="provider" @change="onProviderChange">
            <option v-for="(_, k) in providerPresets" :key="k" :value="k">{{ k }}</option>
          </select>
        </label>
        <label>
          <span class="form-label">名称（备注）</span>
          <input v-model="label" type="text" placeholder="如：工作邮箱" />
        </label>
        <label>
          <span class="form-label">IMAP 服务器</span>
          <input v-model="server" type="text" />
        </label>
        <label>
          <span class="form-label">端口</span>
          <input v-model.number="port" type="number" />
        </label>
        <label>
          <span class="form-label">邮箱地址</span>
          <input v-model="emailUser" type="text" />
        </label>
        <label>
          <span class="form-label">密码 / 授权码</span>
          <input v-model="emailPass" type="password" />
        </label>
      </div>
      <div class="form-footer">
        <button class="btn btn-primary" @click="submit">＋ 保存账号</button>
        <span class="text-muted" style="font-size:0.65rem;">已保存的账号可删除，不影响已有数据</span>
      </div>

      <!-- Existing accounts (delete) -->
      <div v-if="accountStore.accounts.length" class="existing-list">
        <div v-for="a in accountStore.accounts" :key="a.id" class="existing-row">
          <span class="existing-info">{{ a.label || a.email_user }} · {{ a.email_user }} · {{ a.imap_server }}</span>
          <button class="btn btn-danger-outline btn-sm" @click="accountStore.deleteAccount(a.id)">删除</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.account-bar { }

.bar-row {
  display: flex; align-items: center; justify-content: space-between; gap: 1rem;
}

.bar-left { display: flex; align-items: center; gap: 0.6rem; }

.bar-label {
  font-size: 0.75rem; font-weight: 600; color: var(--t3);
  text-transform: uppercase; letter-spacing: 0.3px; white-space: nowrap;
}

.bar-select { width: auto; min-width: 220px; }

.bar-right { display: flex; align-items: center; gap: 0.75rem; }

.account-count { font-size: 0.72rem; color: var(--t4); }

/* Add form */
.add-form {
  margin-top: 1rem; padding-top: 1rem; border-top: 1px solid var(--border);
}

.add-form h5 { margin-bottom: 0.75rem; }

.form-grid {
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.65rem;
}

.form-label { font-size: 0.72rem; color: var(--t4); margin-bottom: 0.15rem; display: block; }

.form-footer {
  display: flex; align-items: center; gap: 1rem; margin-top: 0.75rem;
}

/* Existing accounts */
.existing-list { margin-top: 1rem; }

.existing-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0.35rem 0; border-bottom: 1px solid var(--border-light);
  font-size: 0.78rem;
}

.existing-row:last-child { border-bottom: none; }

.existing-info { color: var(--t3); }

.btn-danger-outline {
  background: transparent; border: 1px solid var(--red); color: var(--red);
  padding: 0.2rem 0.6rem; border-radius: var(--r-sm); font-size: 0.72rem;
  cursor: pointer; transition: all 0.15s;
}

.btn-danger-outline:hover { background: var(--red-bg); }
</style>
