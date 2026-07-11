<script setup lang="ts">
const emit = defineEmits<{ suggest: [text: string] }>()

const suggestions = [
  ["🏢", "客户-项目图谱", "客户、对接人、项目、内部负责人关系", "梳理邮件中出现的客户公司、外部对接人、相关项目和内部负责人"],
  ["⚠️", "风险与延期", "定位项目风险、延期原因和客户反馈", "最近邮件里提到的项目风险、延期原因和客户反馈是什么"],
  ["📎", "合同/报价附件", "按时间、附件和主题筛出关键邮件", "最近七天带附件的邮件中哪些和报价或合同有关"],
  ["📊", "处理看板", "查看入库成功率、失败和待处理数量", "本周邮件入库成功率、失败数量和待处理邮件分别是多少"],
  ["👤", "联系人脉络", "查看某个联系人关联的项目和内部同事", "王总参与了哪些项目，主要对接了哪些内部同事"],
  ["🏷️", "客户反馈", "归纳客户诉求、问题和后续动作", "客户最近反馈了哪些问题，有哪些需要跟进的事项"],
  ["🔎", "失败排查", "快速列出失败邮件和可能原因", "列出最近三天失败的邮件，并说明是否和附件解析或合同内容有关"],
  ["📬", "发件人排行", "找出高频往来对象和邮件占比", "本月谁发邮件最多，前十名分别是多少封"],
]
</script>

<template>
  <div class="chat-hero">
    <div class="ch-logo">M</div>
    <div class="ch-title">你好，我是 MailGraph 助手</div>
    <div class="ch-sub">
      基于 LightRAG 跨文档知识图谱，用自然语言探索邮件里的<br>
      客户、对接人、项目与内部负责人关系
    </div>

    <div class="quick-grid">
      <div
        v-for="(card, i) in suggestions"
        :key="i"
        class="quick-card"
        @click="emit('suggest', card[3])"
      >
        <div class="qc-top">
          <span class="qc-icon">{{ card[0] }}</span>
          <span class="qc-title">{{ card[1] }}</span>
        </div>
        <div class="qc-desc">{{ card[2] }}</div>
        <div class="qc-query">{{ card[3] }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-hero {
  max-width: 720px; margin: 3.5rem auto 1.25rem auto; text-align: center;
}

.ch-logo {
  width: 54px; height: 54px; border-radius: 13px;
  background: #23795F; color: #fff; font-size: 1.5rem;
  font-weight: 800; letter-spacing: -1px;
  display: flex; align-items: center; justify-content: center;
  margin: 0 auto 1rem auto; box-shadow: var(--sh-md);
}

.ch-title {
  font-size: 1.5rem; font-weight: 700; color: var(--t1);
  letter-spacing: -0.4px; margin-bottom: 0.4rem;
}

.ch-sub {
  font-size: 0.9rem; color: var(--t3); line-height: 1.6; margin-bottom: 2rem;
}

.quick-grid {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.75rem;
  text-align: left;
}

.quick-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 0.85rem 0.9rem;
  box-shadow: var(--sh-sm); cursor: pointer;
  transition: box-shadow 0.15s, transform 0.1s;
  min-height: 126px;
}

.quick-card:hover { box-shadow: var(--sh-md); transform: translateY(-1px); }

.qc-top { display: flex; align-items: center; gap: 0.45rem; margin-bottom: 0.45rem; }
.qc-icon { font-size: 1rem; }
.qc-title { font-size: 0.86rem; font-weight: 650; color: var(--t1); }
.qc-desc { font-size: 0.73rem; color: var(--t3); line-height: 1.45; min-height: 2.1rem; }
.qc-query { font-size: 0.68rem; color: var(--t4); line-height: 1.45; margin-top: 0.45rem; }
</style>
