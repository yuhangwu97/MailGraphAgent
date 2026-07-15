<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAccountStore } from '@/stores/account'

const emit = defineEmits<{
  (e: 'open-drawer'): void
  (e: 'search', q: string): void
}>()

const accountStore = useAccountStore()
const searchText = defineModel<string>('searchText', { default: '' })
const showAccountForm = ref(false)

// ── Add account form ──
const providerPresets: Record<string, [string, number]> = {
  'Gmail': ['imap.gmail.com', 993],
  'Outlook': ['outlook.office365.com', 993],
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
  showAccountForm.value = false
}

onMounted(async () => {
  if (!accountStore.accounts.length) await accountStore.fetchAccounts()
  if (!accountStore.accounts.length) showAccountForm.value = true
})
</script>

<template>
  <div class="topbar-wrapper">
    <div class="topbar">
      <div class="topbar-left">
        <!-- Account selector -->
        <div class="acct-group">
          <label class="acct-label">处理邮箱</label>
          <select
            v-if="accountStore.accounts.length"
            :value="accountStore.activeId"
            @change="accountStore.activeId = ($event.target as HTMLSelectElement).value"
            class="acct-select"
          >
            <option v-for="a in accountStore.accounts" :key="a.id" :value="a.id">
              {{ a.email_user }}{{ a.label ? ` · ${a.label}` : '' }}
            </option>
          </select>
          <span v-else class="acct-none">未配置邮箱</span>
          <button
            class="acct-mgmt-btn"
            @click="showAccountForm = !showAccountForm"
            :title="showAccountForm ? '收起' : '管理账号'"
          >
            ⚙️
          </button>
        </div>
      </div>

      <div class="topbar-actions">
        <button class="tb-action-btn" @click="emit('open-drawer')">
          <span class="tb-action-icon">📥</span>
          导入邮件
        </button>
      </div>

      <div class="topbar-right">
        <div class="search-wrap">
          <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          <input
            v-model="searchText"
            class="search-input"
            type="text"
            placeholder="搜索主题或发件人…"
            @input="emit('search', searchText)"
          />
        </div>
      </div>
    </div>

    <!-- Account management form (collapsible) -->
    <div v-if="showAccountForm" class="acct-form">
      <div class="acct-form-header">
        <h5>添加邮箱账号</h5>
        <span class="acct-count" v-if="accountStore.accounts.length">
          {{ accountStore.accounts.length }} 个已配置
        </span>
      </div>

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
        <span class="form-footer-hint">密码仅用于 IMAP 登录，加密存储</span>
      </div>

      <!-- Existing accounts list -->
      <div v-if="accountStore.accounts.length" class="existing-list">
        <div class="existing-section-title">已配置账号</div>
        <div v-for="a in accountStore.accounts" :key="a.id" class="existing-row">
          <span class="existing-info">
            {{ a.email_user }}{{ a.label ? ` · ${a.label}` : '' }} · {{ a.email_user }} · {{ a.imap_server }}
            <span v-if="a.is_default" class="default-badge">默认</span>
          </span>
          <span class="existing-actions">
            <button
              v-if="!a.is_default"
              class="btn btn-secondary btn-sm"
              @click="accountStore.setDefault(a.id)"
            >设为默认</button>
            <button class="btn-danger-outline" @click="accountStore.deleteAccount(a.id)">删除</button>
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.topbar-wrapper {
  flex-shrink: 0;
}

.topbar {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.6rem 0;
  margin-bottom: 0.5rem;
  border-bottom: 1px solid var(--border);
}

.topbar-left { flex-shrink: 0; }

.acct-group {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.acct-label {
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--t4);
  text-transform: uppercase;
  letter-spacing: 0.3px;
  white-space: nowrap;
}

.acct-select {
  width: auto;
  min-width: 180px;
  padding: 0.3rem 0.5rem;
  font-size: 0.78rem;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--surface);
  font-family: inherit;
  cursor: pointer;
}

.acct-none {
  font-size: 0.78rem;
  color: var(--t4);
}

.acct-mgmt-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--surface);
  cursor: pointer;
  font-size: 0.8rem;
  transition: all var(--dur-fast) var(--ease);
}

.acct-mgmt-btn:hover {
  background: var(--surface-2);
  border-color: var(--t5);
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-shrink: 0;
}

.tb-action-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.35rem 0.7rem;
  font-size: 0.78rem;
  font-weight: 500;
  font-family: inherit;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--t2);
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease);
  white-space: nowrap;
}

.tb-action-btn:hover {
  background: var(--surface-2);
  border-color: var(--t5);
}

.tb-action-icon { font-size: 0.85rem; }

.topbar-right {
  margin-left: auto;
  flex-shrink: 0;
}

.search-wrap {
  position: relative;
  display: flex;
  align-items: center;
}

.search-icon {
  position: absolute;
  left: 8px;
  color: var(--t4);
  pointer-events: none;
}

.search-input {
  width: 220px;
  padding: 0.3rem 0.5rem 0.3rem 28px !important;
  font-size: 0.76rem !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--r-sm) !important;
  background: var(--surface) !important;
  font-family: inherit;
}

.search-input:focus {
  border-color: var(--p) !important;
  box-shadow: 0 0 0 2px var(--p-ring) !important;
  outline: none;
}

/* ── Account form ── */
.acct-form {
  margin-bottom: 1rem;
  padding: 1rem 1.1rem;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--surface);
}

.acct-form-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.75rem;
}

.acct-form-header h5 {
  margin: 0;
  font-size: 0.88rem;
  font-weight: 650;
  color: var(--t1);
}

.acct-count {
  font-size: 0.68rem;
  color: var(--t4);
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.65rem;
}

.form-label {
  font-size: 0.68rem;
  font-weight: 600;
  color: var(--t4);
  text-transform: uppercase;
  letter-spacing: 0.3px;
  margin-bottom: 0.15rem;
  display: block;
}

.form-grid input,
.form-grid select {
  width: 100%;
  padding: 0.4rem 0.6rem;
  font-size: 0.8rem;
}

.form-footer {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-top: 0.75rem;
}

.form-footer-hint {
  font-size: 0.65rem;
  color: var(--t4);
}

/* ── Existing accounts ── */
.existing-list {
  margin-top: 1rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--border-light);
}

.existing-section-title {
  font-size: 0.68rem;
  font-weight: 600;
  color: var(--t4);
  text-transform: uppercase;
  letter-spacing: 0.3px;
  margin-bottom: 0.4rem;
}

.existing-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.35rem 0;
  border-bottom: 1px solid var(--border-light);
  font-size: 0.78rem;
}

.existing-row:last-child {
  border-bottom: none;
}

.existing-info {
  color: var(--t3);
  display: flex;
  align-items: center;
  gap: 0.4rem;
  min-width: 0;
  overflow: hidden;
}

.existing-actions {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-shrink: 0;
}

.default-badge {
  font-size: 0.6rem;
  font-weight: 700;
  color: var(--p);
  background: var(--p-light);
  padding: 0.05rem 0.4rem;
  border-radius: 9999px;
  white-space: nowrap;
}

.btn-danger-outline {
  background: transparent;
  border: 1px solid var(--red);
  color: var(--red);
  padding: 0.25rem 0.6rem;
  border-radius: var(--r-sm);
  font-size: 0.72rem;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
}

.btn-danger-outline:hover {
  background: var(--red-bg);
}

@media (max-width: 900px) {
  .topbar {
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  .topbar-right {
    width: 100%;
  }
  .search-wrap { width: 100%; }
  .search-input { width: 100% !important; }
  .form-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 600px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
