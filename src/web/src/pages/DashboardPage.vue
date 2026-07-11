<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { mailsApi, graphApi, type MailStats } from '@/api'
import SvgIcon from '@/components/SvgIcon.vue'
import KpiCards from '@/components/dashboard/KpiCards.vue'
import ProjectCard from '@/components/dashboard/ProjectCard.vue'

const kpi = ref<MailStats>({ total: 0, done: 0, pending: 0, failed: 0, skipped: 0, ingested: 0, indexed: 0 })
const graphNodes = ref(0)
const projectCount = ref(0)
const contactCount = ref(0)
const projects = ref<any[]>([])
const search = ref('')
const loading = ref(true)

// LightRAG 存储的 entity_type 统一为小写去空格，故一律按小写比较。
// 人员相关类型放宽匹配（person / contact / employee 都算联系人）。
const PEOPLE_TYPES = ['person', 'contact', 'employee']
const etype = (e: any) => String(e?.type || '').toLowerCase()

onMounted(async () => {
  try {
    const [stats, entRes, relRes] = await Promise.all([
      mailsApi.stats(),
      graphApi.entities(1, 500),
      graphApi.relationships(1, 2000),
    ])
    kpi.value = stats
    const entities = entRes.entities || []
    const rels = relRes.relationships || []
    graphNodes.value = entities.length
    projectCount.value = entities.filter((e: any) => etype(e) === 'project').length
    contactCount.value = entities.filter((e: any) => PEOPLE_TYPES.includes(etype(e))).length

    // 实体按 id 与 name 双键索引，关系边的端点可能用其中任一标识
    const byKey = new Map<string, any>()
    for (const e of entities) {
      if (e.id) byKey.set(String(e.id), e)
      if (e.name) byKey.set(String(e.name), e)
    }

    // 为每个项目收集其在图谱中的邻居实体
    const neighborsOf = (proj: any): any[] => {
      const keys = new Set([String(proj.id), String(proj.name)].filter(Boolean))
      const out: any[] = []
      const seen = new Set<string>()
      for (const r of rels) {
        const s = String(r.source_id ?? '')
        const t = String(r.target_id ?? '')
        let otherKey = ''
        if (keys.has(s)) otherKey = t
        else if (keys.has(t)) otherKey = s
        if (!otherKey) continue
        const ent = byKey.get(otherKey)
        if (!ent || !ent.name) continue
        const dedupe = String(ent.id || ent.name)
        if (seen.has(dedupe)) continue
        seen.add(dedupe)
        out.push(ent)
      }
      return out
    }

    const clean = (s: string) => (s || '').replace(/<[^>]*>/g, '')

    projects.value = entities
      .filter((e: any) => etype(e) === 'project')
      .map((p: any) => {
        const neighbors = neighborsOf(p)
        return {
          name: clean(p.name),
          description: clean((p.description || '') + '').slice(0, 200),
          people: neighbors
            .filter((n: any) => PEOPLE_TYPES.includes(etype(n)))
            .map((n: any) => ({ name: clean(n.name) })),
          companies: neighbors
            .filter((n: any) => etype(n) === 'organization')
            .map((n: any) => ({ name: clean(n.name) })),
        }
      })
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
})

const filtered = () => {
  if (!search.value) return projects.value
  const q = search.value.toLowerCase()
  return projects.value.filter((p: any) => p.name.toLowerCase().includes(q))
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h2>项目看板</h2>
        <p class="page-desc">基于邮件内容自动识别的项目、人员与组织关系</p>
      </div>
      <div class="search-wrap" v-if="projects.length > 0">
        <SvgIcon name="search" :size="15" class="search-icon" />
        <input
          v-model="search"
          type="text"
          placeholder="搜索项目..."
          class="search-input-inline"
        />
      </div>
    </div>

    <KpiCards
      :total-mails="kpi.total"
      :done-mails="kpi.done"
      :graph-nodes="graphNodes"
      :projects="projectCount"
      :contacts="contactCount"
    />

    <!-- Loading skeleton -->
    <div v-if="loading" class="skeleton-grid">
      <div v-for="i in 6" :key="i" class="skeleton-card">
        <div class="sk-line sk-title"></div>
        <div class="sk-line sk-body"></div>
        <div class="sk-line sk-body short"></div>
        <div class="sk-line sk-meta"></div>
      </div>
    </div>

    <!-- Empty state -->
    <div v-else-if="!projects.length" class="empty-state">
      <div class="empty-icon">
        <SvgIcon name="inbox" :size="32" />
      </div>
      <h3>暂无项目数据</h3>
      <p>请先在「邮件工作台」拉取邮件并导入到知识图谱，系统将自动识别项目信息。</p>
    </div>

    <!-- Project grid -->
    <div v-else class="project-grid">
      <ProjectCard
        v-for="p in filtered()"
        :key="p.name"
        :name="p.name"
        :description="p.description"
        :people="p.people"
        :companies="p.companies"
      />
    </div>

    <p v-if="!loading && projects.length > 0 && !filtered().length" class="text-muted" style="text-align:center;margin-top:2rem;">
      没有匹配「{{ search }}」的项目
    </p>
  </div>
</template>

<style scoped>
.page-head {
  display: flex; align-items: flex-start; justify-content: space-between;
  margin-bottom: 1.25rem; gap: 1rem; flex-wrap: wrap;
}

.page-desc {
  color: var(--t3); font-size: 0.82rem; margin-top: 0.15rem;
}

.search-wrap {
  position: relative;
  flex-shrink: 0;
}

.search-icon {
  position: absolute; left: 10px; top: 50%; transform: translateY(-50%);
  color: var(--t4); pointer-events: none;
}

.search-input-inline {
  padding-left: 2rem !important;
  max-width: 240px;
  font-size: 0.8rem !important;
  height: 34px;
}

.project-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 0.85rem;
}

/* Skeleton loading */
.skeleton-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 0.85rem;
}

.skeleton-card {
  background: var(--surface);
  border: 1px solid var(--border-light);
  border-radius: var(--r);
  padding: 1.15rem 1.25rem;
}

.sk-line {
  height: 12px; border-radius: 4px;
  background: var(--border-light);
  margin-bottom: 0.55rem;
  animation: shimmer 1.6s infinite;
}

.sk-title { width: 55%; height: 15px; }
.sk-body { width: 90%; }
.sk-body.short { width: 65%; }
.sk-meta { width: 40%; height: 10px; }

@keyframes shimmer {
  0% { opacity: 0.5; }
  50% { opacity: 1; }
  100% { opacity: 0.5; }
}

/* Empty state */
.empty-state {
  text-align: center; padding: 3rem 1.5rem;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r);
}

.empty-icon {
  width: 56px; height: 56px; border-radius: 14px;
  background: var(--surface-2); color: var(--t4);
  display: flex; align-items: center; justify-content: center;
  margin: 0 auto 1rem;
}

.empty-state h3 {
  color: var(--t2); margin-bottom: 0.35rem;
}

.empty-state p {
  color: var(--t4); font-size: 0.82rem; max-width: 380px;
  margin: 0 auto; line-height: 1.5;
}
</style>
