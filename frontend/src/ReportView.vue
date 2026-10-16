<script setup lang="ts">
import type { RunReport } from './store'

const props = defineProps<{ report: RunReport }>()
const emit = defineEmits<{ (e:'close'):void }>()

const weekdayNames = ['周一','周二','周三','周四','周五','周六','周日']
function windowText(days:number[], start:number, end:number) {
  const name = days.length === 5 && days.every(d => d < 5) ? '工作日'
    : days.every(d => d > 4) ? '周末' : '指定日期'
  return `${name} ${start}:00–${end}:00`
}
// JS getDay()：0=周日；项目约定 0=周一，需要换算。
function slotWeekday(date:string) {
  const jsDay = new Date(date + 'T00:00:00').getDay()
  return weekdayNames[jsDay === 0 ? 6 : jsDay - 1]
}
function slotText(slot:{date:string;start:number;end:number}) {
  return `${slot.date}（${slotWeekday(slot.date)}）${slot.start}:00–${slot.end}:00`
}
function printReport() { window.print() }
</script>

<template>
  <div class="report-screen">
    <div class="report-actions">
      <button class="plain" @click="emit('close')">关闭</button>
      <button class="primary" @click="printReport()">打印 / 另存为 PDF</button>
    </div>
    <article class="report-sheet">
      <header class="report-head">
        <div class="report-brand"><span class="brand-mark">合</span><strong>合练 · 教练选择决策报告</strong></div>
        <dl class="report-meta">
          <div><dt>账号</dt><dd>{{ report.username || '—' }}</dd></div>
          <div><dt>生成时间</dt><dd>{{ report.generated_at.slice(0,19).replace('T',' ') }}</dd></div>
          <div><dt>运行状态</dt><dd>{{ report.status }}</dd></div>
          <div><dt>需求版本</dt><dd>v{{ report.version }} · 知识版本 {{ report.knowledge_version ?? '—' }}</dd></div>
          <div><dt>来源模式</dt><dd>{{ report.mode === 'demo' ? '演示数据' : '真实服务' }}</dd></div>
        </dl>
      </header>

      <p v-if="report.notice" class="report-notice">{{ report.notice }}</p>
      <p v-if="report.degraded.length" class="report-notice">{{ report.degraded.join('；') }}</p>

      <section>
        <h2>需求与条件</h2>
        <dl class="report-requirements">
          <div><dt>训练目标</dt><dd>{{ report.requirements.goal || '未指定' }}</dd></div>
          <div><dt>每课预算</dt><dd>{{ report.requirements.budget_max != null ? '≤ ¥' + report.requirements.budget_max : '未限制' }}</dd></div>
          <div><dt>上课区域</dt><dd>{{ report.requirements.district || '未限制' }}</dd></div>
          <div><dt>上课方式</dt><dd>{{ report.requirements.delivery_mode || '未指定' }}</dd></div>
          <div><dt>可用时间</dt><dd>{{ report.requirements.available_windows.length ? report.requirements.available_windows.map(w => windowText(w.days, w.start, w.end)).join('；') : '未提供' }}</dd></div>
          <div><dt>教学偏好</dt><dd>{{ report.requirements.preferences.join('、') || '无' }}</dd></div>
          <div v-if="report.requirements.self_reported_conditions.length"><dt>自述情况</dt><dd>{{ report.requirements.self_reported_conditions.join('、') }}</dd></div>
        </dl>
        <p class="report-quote-source">原始请求：{{ report.message }}</p>
      </section>

      <section v-if="report.summary"><h2>结论摘要</h2><p>{{ report.summary }}</p></section>

      <section v-if="report.candidates.length">
        <h2>候选对比（{{ report.candidates.length }} 位）</h2>
        <table class="report-table">
          <thead><tr><th>教练</th><th>价格 / 区域</th><th>专长</th><th>硬条件</th><th>排序参考分</th></tr></thead>
          <tbody>
            <tr v-for="c in report.candidates" :key="c.id">
              <td><strong>{{ c.name }}</strong><div class="muted">{{ c.is_demo ? '虚构样例' : '教练资料' }} · {{ c.delivery_modes.join(' / ') }}</div></td>
              <td>¥{{ c.price }} / 课<div class="muted">{{ c.districts.join('、') }}</div></td>
              <td>{{ c.specialties.join('、') || '—' }}</td>
              <td><span v-for="(ok,key) in c.hard_matches" :key="key" :class="['report-pill', ok ? 'ok' : 'bad']">{{ ok ? '✓' : '×' }} {{ key }}</span></td>
              <td>{{ c.score }} <div class="muted">{{ c.score_label }}</div></td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="report.candidates.length">
        <h2>逐条依据</h2>
        <div v-for="c in report.candidates" :key="c.id" class="report-coach">
          <h3>{{ c.name }}</h3>
          <template v-if="c.reasons.length">
            <div v-for="(r,i) in c.reasons" :key="i" class="report-reason">
              <p>{{ r.text }}</p>
              <blockquote>{{ r.quote }}</blockquote>
              <small>来源：{{ r.source_title || '—' }}<span v-if="r.source"> · {{ r.source }}</span></small>
            </div>
          </template>
          <p v-else class="muted">当前未找到可引用的相关资料。</p>
          <p class="report-breakdown">评分构成：<span v-for="(v,k) in c.score_breakdown" :key="k">{{ k }} {{ v }} </span></p>
        </div>
      </section>

      <section v-if="report.candidates.some(c => c.pending.length)">
        <h2>待确认项</h2>
        <div v-for="c in report.candidates" :key="c.id" v-show="c.pending.length" class="report-coach">
          <h3>{{ c.name }}</h3>
          <ul><li v-for="p in c.pending" :key="p">{{ p }}</li></ul>
        </div>
      </section>

      <section v-if="report.preregistration">
        <h2>预登记</h2>
        <p>已为「{{ report.preregistration.coach?.name }}」登记：</p>
        <ul><li v-for="s in report.preregistration.slots" :key="s.date + s.start">{{ slotText(s) }}</li></ul>
        <p class="report-status">{{ report.preregistration.status_label }}</p>
      </section>

      <section v-if="report.knowledge">
        <h2>资料问答</h2>
        <p class="report-answer">{{ report.knowledge.answer }}</p>
        <p class="report-quote-source">依据{{ report.knowledge.source_type === 'knowledge_base' ? '平台资料' : '模型通用知识' }}，引用 {{ report.knowledge.citations.length }} 条。</p>
      </section>

      <section v-if="report.evidence.length">
        <h2>证据引用列表</h2>
        <ol class="report-evidence">
          <li v-for="e in report.evidence" :key="e.id">
            <strong>{{ e.source_title }}</strong> · 版本 {{ e.version }}
            <div class="muted">{{ e.source }}<span v-if="e.coach_id"> · 教练 {{ e.coach_id }}</span></div>
          </li>
        </ol>
      </section>

      <footer class="report-foot">
        <p>本报告的教练资料与案例为演示样本；排序参考分用于排序，不代表成功概率，也不构成医疗或康复判断。每位推荐理由均对应可持续核实的资料原文。</p>
        <p>运行 {{ report.run_id }} · 会话 {{ report.session_id }}</p>
      </footer>
    </article>
  </div>
</template>
