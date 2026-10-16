<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useAuth, useChat, useAccount, api, setUnauthorizedHandler, type Candidate, type Evidence, type RunReport } from './store'
import ReportView from './ReportView.vue'
import CoachDetail from './CoachDetail.vue'
import AccountPanel from './AccountPanel.vue'
import KnowledgeBase from './KnowledgeBase.vue'
const auth = useAuth()
const chat = useChat()
const account = useAccount()
const input = ref('')
const replyText = ref('')
const selected = ref<string[]>([])
const opened = ref<Evidence | null>(null)
const editing = ref(false)
const budget = ref('')
const district = ref('')
const debug = ref(false)
const sendingSource = ref(false)
const authForm = ref({username:'', password:''})
const authMode = ref<'login' | 'register'>('login')
const authError = ref('')
const report = ref<RunReport | null>(null)
const detailCoach = ref<{id:string; name:string} | null>(null)
const accountOpen = ref(false)
const kbOpen = ref(false)
const exporting = ref(false)
const req = computed(() => chat.session?.requirements || {})
const latestResults = computed(() => chat.session?.runs.filter(r => r.result).at(-1))
const examples = ['我想减脂，预算每节课300元以内，南山区，工作日晚上有时间。我膝盖不舒服，希望教练耐心。', '什么是渐进超负荷？', '平台减脂课程怎么安排？', '帮我推荐教练']
const statusText:Record<string,string> = {created:'准备中',running:'处理中',waiting_for_user:'等待补充',completed:'已完成',failed:'未完成',cancelled:'已取消'}
const initials = (c:Candidate) => c.name.slice(-2)
function slots(c:Candidate) {return c.availability.map(s => `${s.days.every(d => d < 5) ? '工作日' : s.days.every(d => d > 4) ? '周末' : '部分日期'} ${s.start}:00–${s.end}:00`).join(' / ')}
function shortTime(value:string) {
  const date = new Date(value)
  const now = new Date()
  const sameDay = date.toDateString() === now.toDateString()
  return sameDay ? date.toTimeString().slice(0,5) : `${date.getMonth()+1}/${date.getDate()}`
}
async function submitAuth() {
  const {username, password} = authForm.value
  if (!username.trim() || !password) return
  auth.pending = true; authError.value = ''
  try {
    if (authMode.value === 'login') await auth.login(username.trim(), password)
    else await auth.register(username.trim(), password)
    authForm.value = {username:'', password:''}
    await chat.initialize()
  } catch (e) { authError.value = (e as Error).message }
  finally { auth.pending = false }
}
async function logout() {
  chat.reset()
  account.reset()
  await auth.logout()
  authMode.value = 'login'; authError.value = ''
}
async function submit() {
  const value = input.value.trim()
  if (!value) return
  input.value = ''
  if (chat.current?.status === 'waiting_for_user') await chat.reply(value)
  else await chat.send(value)
}
function edit() {budget.value = req.value.budget_max?.toString() || ''; district.value = req.value.district || ''; editing.value = true}
async function apply() {
  const patch:Record<string,unknown> = {}
  const nextBudget = budget.value === '' ? null : Number(budget.value)
  if (nextBudget !== null && (!Number.isFinite(nextBudget) || nextBudget < 0)) {chat.error = '预算请输入非负数字'; return}
  if (nextBudget !== (req.value.budget_max ?? null)) patch.budget_max = nextBudget
  if ((district.value || null) !== (req.value.district ?? null)) patch.district = district.value || null
  editing.value = false
  if (Object.keys(patch).length) await chat.send('调整条件，其他条件不变', patch)
}
async function compare() {if(selected.value.length >= 2) await chat.send('比较这两位教练', undefined, selected.value)}
async function source(id:string, version?:string) {
  sendingSource.value = true
  const reference = chat.session?.runs.flatMap(r => [...(r.result?.evidence || []), ...(r.result?.knowledge?.evidence || [])]).find(e => e.id === id)
  try {opened.value = await api<Evidence>('/evidence/' + id + '?version=' + encodeURIComponent(version || reference?.version || ''))}
  catch(e) {chat.error = (e as Error).message}
  finally {sendingSource.value = false}
}
async function exportReport(runId:string) {
  exporting.value = true
  try {report.value = await api<RunReport>('/runs/' + runId + '/report')}
  catch(e) {chat.error = (e as Error).message}
  finally {exporting.value = false}
}
function openCoach(id:string, name:string) {detailCoach.value = {id, name}}
async function openAccount() {
  accountOpen.value = true
  await account.load()
}
async function toggleFavorite(id:string) {await account.toggleFavorite(id)}
watch(() => chat.current?.id, () => {selected.value = []; replyText.value = ''})
onMounted(async () => {
  setUnauthorizedHandler(() => {chat.reset(); account.reset(); auth.user = null})
  await auth.check()
  if (auth.user) await chat.initialize()
})
onUnmounted(() => {setUnauthorizedHandler(null); chat.disconnect()})
</script>

<template>
  <div v-if="!auth.checked" class="shell auth-shell"><div class="auth-card"><span class="brand-mark">合</span><p class="muted">正在连接…</p></div></div>
  <div v-else-if="!auth.user" class="shell auth-shell">
    <div class="auth-card">
      <a class="brand" href="#" @click.prevent><span class="brand-mark">合</span><span>合练<span class="brand-en">COACH GUIDE</span></span></a>
      <h1>{{authMode === 'login' ? '欢迎回来' : '创建账号'}}</h1>
      <p class="muted">{{authMode === 'login' ? '登录后查看和继续你的训练对话。' : '注册后，聊天记录会保存在你的账号下。'}}</p>
      <form class="edit-form auth-form" @submit.prevent="submitAuth">
        <label>用户名<input v-model="authForm.username" autocomplete="username" minlength="3" maxlength="32" required placeholder="3–32 个字符，不含空格"></label>
        <label>密码<input v-model="authForm.password" type="password" :autocomplete="authMode === 'login' ? 'current-password' : 'new-password'" :minlength="authMode === 'register' ? 8 : 1" maxlength="128" required :placeholder="authMode === 'register' ? '至少 8 个字符' : '输入密码'"></label>
        <div v-if="authError" class="error-banner" role="alert">{{authError}}</div>
        <button class="primary" type="submit" :disabled="auth.pending">{{authMode === 'login' ? '登录' : '注册并进入'}}</button>
      </form>
      <button class="text-button auth-switch" @click="authMode = authMode === 'login' ? 'register' : 'login'; authError=''">
        {{authMode === 'login' ? '没有账号？注册一个' : '已有账号？直接登录'}}
      </button>
    </div>
  </div>
  <div v-else class="shell">
    <header class="topbar">
      <a class="brand" href="#" @click.prevent><span class="brand-mark">合</span><span>合练<span class="brand-en">COACH GUIDE</span></span></a>
      <div class="header-actions"><span class="mode" :class="{live:chat.health?.mode === 'live'}"><i></i>{{ chat.health ? (chat.health.mode === 'demo' ? '演示体验' : '真实服务') : '正在连接' }}</span><button class="user-chip" :title="auth.user.username + ' · 我的账号'" @click="openAccount">👤 {{auth.user.username}}</button><button class="plain" @click="logout">退出</button><button class="plain" @click="chat.newSession().catch(e => chat.error=e.message)">＋ 新对话</button></div>
    </header>
    <div v-if="chat.health?.mode==='demo'" class="demo-strip">当前使用虚构教练与模拟模型，用于体验选择流程。正式资料可导入后切换真实服务。</div>
    <div v-if="chat.health?.mode==='live' && !chat.health.model_configured" class="error-banner">模型密钥尚未配置。请在项目 .env 中填写对话模型（阿里百炼）密钥后重启后端。</div>
    <div v-if="chat.error" class="error-banner" role="alert">{{chat.error}} <button class="plain" @click="chat.error=''">关闭</button></div>
    <main class="layout">
      <aside class="sidebar">
        <div class="eyebrow">YOUR TRAINING, YOUR WAY</div>
        <h1>找到合拍的教练。<br><span>从你的需求开始。</span></h1>
        <p class="intro">说说目标、预算和时间。我们帮你核对资料，把选择的依据摆在眼前。</p>
        <section class="history">
          <div class="section-title"><h2>历史对话</h2><button class="text-button" @click="chat.newSession().catch(e => chat.error=e.message)">＋ 新对话</button></div>
          <ul v-if="chat.sessions.length">
            <li v-for="item in chat.sessions" :key="item.id" :class="{active:item.id === chat.session?.id}">
              <button class="history-open" @click="chat.switchSession(item.id)" :title="item.title"><span class="history-title">{{item.title}}</span><span class="history-time">{{shortTime(item.updated_at)}}</span></button>              <button class="history-delete" @click="chat.removeSession(item.id)" :aria-label="'删除对话：' + item.title" title="删除">×</button>
            </li>
          </ul>
          <p v-else class="muted history-empty">还没有记录，发送第一条消息吧。</p>
        </section>
        <section class="requirements">
          <div class="section-title"><h2>你的训练需求</h2><button class="text-button" @click="edit">修改</button></div>
          <dl v-if="!editing"><div><dt>训练目标</dt><dd>{{req.goal || '等你告诉我们'}}</dd></div><div><dt>每课预算</dt><dd>{{req.budget_max != null ? '≤ ¥' + req.budget_max : '尚未限制'}}</dd></div><div><dt>上课区域</dt><dd>{{req.district || '尚未限制'}}</dd></div><div><dt>可用时间</dt><dd>{{req.available_windows?.length ? req.available_windows.map(w => `${w.days.every(d=>d<5)?'工作日':w.days.every(d=>d>4)?'周末':'指定日期'} ${w.start}–${w.end}点`).join('；') : '待确认'}}</dd></div><div><dt>上课方式</dt><dd>{{req.delivery_mode || '待确认'}}</dd></div></dl>
          <form v-else class="edit-form" @submit.prevent="apply"><label>每课预算（空白表示不限）<input type="number" min="0" v-model="budget"></label><label>区域<select v-model="district"><option value="">不限区域</option><option>南山区</option><option>福田区</option><option>宝安区</option><option>罗湖区</option></select></label><div class="button-row"><button class="primary" type="submit">保存并重新查找</button><button type="button" class="plain" @click="editing=false">取消</button></div></form>
          <div v-if="req.preferences?.length" class="tags"><span v-for="p in req.preferences" :key="p">{{p}}</span></div>
          <p v-if="req.self_reported_conditions?.length" class="condition-note">已留意：{{req.self_reported_conditions.join('、')}}</p>
          <div class="version">需求版本 {{req.version || 0}} · 香港时间</div>
        </section>
        <button class="kb-entry" @click="kbOpen=true">
          <span class="kb-entry-icon">📚</span>
          <span class="kb-entry-body"><strong>我的知识库</strong><small>上传 PDF / Markdown，基于原文问答带引用</small></span>
          <span class="kb-entry-arrow">↗</span>
        </button>
        <div class="trust-note"><div class="trust-symbol">✓</div><p><strong>每个选择都有依据</strong><br>价格和时间核对当前资料，推荐理由可以展开查看原文。</p></div>
        <button class="debug-button" @click="debug=!debug">{{debug ? '收起' : '查看'}}执行记录 ↗</button>
        <div v-if="debug" class="debug-panel"><p>模式：{{chat.health?.mode}} · Redis {{chat.health?.redis?'可用':'未连接，数据库保留状态'}}</p><pre>{{JSON.stringify(chat.progress,null,2)}}</pre></div>
      </aside>
      <section class="workspace">
        <div class="workspace-heading"><span class="eyebrow">一起把训练安排好</span><span class="step-label">了解需求 → 核对资料 → 比较选择</span></div>
        <div class="conversation">
          <div v-if="!chat.session?.runs.length" class="welcome"><span class="welcome-icon">✳</span><h2>你想从哪里开始？</h2><p>可以找教练，也可以先聊聊怎么训练。</p><div class="example-grid"><button v-for="(example,i) in examples" :key="example" @click="chat.send(example)"><span>{{['01 / 按条件找教练','02 / 了解训练知识','03 / 查看平台课程','04 / 一起明确需求'][i]}}</span>{{i===0?'减脂 · 南山 · 工作日晚上':example}}<b>↗</b></button></div></div>
          <article class="turn" v-for="run in chat.session?.runs" :key="run.id">
            <div class="user-message"><span>你</span><p>{{run.message}}</p></div>
            <div class="assistant-message"><div class="assistant-label"><span class="small-mark">合</span>合练助手 <span class="status-tag" :class="run.status">{{statusText[run.status]}}</span><small>v{{run.version}}</small></div>
              <p v-if="run.status==='running' || run.status==='created'" class="working"><span class="spinner"></span>{{chat.progress.at(-1)?.data.message || '正在确认你的需求…'}}</p>
              <div v-if="run.error" class="failure">{{run.error}}<button class="text-button" @click="chat.send(run.message)">重新尝试</button></div>
              <div v-if="run.clarification && run.id===chat.current?.id" class="clarification"><div class="eyebrow">需要你来决定</div><p>{{run.clarification.question}}</p><div class="choice-options"><button v-for="option in run.clarification.options" :key="option" @click="chat.reply(option)" :disabled="chat.posting">{{option}}</button></div><form @submit.prevent="chat.reply(replyText);replyText=''"><input v-model="replyText" placeholder="也可以补充你自己的想法" required><button class="primary" :disabled="chat.posting">提交</button></form></div>
              <template v-if="run.result">
                <p class="result-summary">{{run.result.summary}}</p>
                <div v-if="run.result.preregistration" class="prereg-result">
                  <div class="prereg-head"><strong>{{run.result.preregistration.coach?.name}}</strong><span class="reg-badge">{{run.result.preregistration.status_label}}</span></div>
                  <ul><li v-for="s in run.result.preregistration.slots" :key="s.date + s.start">{{s.date}} {{s.start}}:00–{{s.end}}:00</li></ul>
                  <button class="text-button" @click="openAccount">在「我的账号」查看四周登记表 ↗</button>
                </div>
                <div v-if="run.result.knowledge" class="knowledge"><div class="answer-text">{{run.result.knowledge.answer}}</div><div class="source-row"><span>{{run.result.knowledge.source_type==='knowledge_base'?'依据平台资料':'来自模型通用知识'}}</span><button v-for="(id,i) in run.result.knowledge.citations" :key="id" @click="source(id)">来源 {{i+1}} ↗</button></div></div>
                <div v-if="run.result.candidates.length" class="candidate-results">
                  <div class="candidate-toolbar"><h3><span v-if="run.result.intents?.includes('coach_comparison')" class="compare-badge">教练对比</span>{{run.result.candidates.length}} 位候选教练{{run.result.intents?.includes('coach_comparison') ? '（逐项对照）' : ''}}</h3><div class="toolbar-actions"><button v-if="run.id===latestResults?.id" class="plain" :disabled="selected.length<2 || chat.busy" @click="compare">比较已选 {{selected.length}} 位 ↗</button><button class="plain" :disabled="exporting" @click="exportReport(run.id)">导出决策报告 PDF ↗</button></div></div>
                  <div class="table-scroll"><table><thead><tr><th>教练 / 选择</th><th>价格与地点</th><th>适合程度与依据</th><th>时间与待确认</th></tr></thead><tbody><tr v-for="(c,i) in run.result.candidates" :key="c.id"><td><div class="coach-name"><input v-if="run.id===latestResults?.id" type="checkbox" :value="c.id" v-model="selected" :aria-label="'选择'+c.name" :disabled="selected.length>=4 && !selected.includes(c.id)"><button class="avatar" :class="'color-'+i%4" @click="openCoach(c.id, c.name)" aria-label="查看教练详情">{{initials(c)}}</button></div><button class="coach-link" @click="openCoach(c.id, c.name)">{{c.name}}</button><div class="muted">{{c.is_demo?'虚构样例':'教练资料'}} · {{c.delivery_modes.join(' / ')}}</div><div class="mini-tags"><span v-for="tag in c.specialties.slice(0,3)" :key="tag">{{tag}}</span></div><div class="row-actions"><button class="plain tiny" :class="{faved:account.favoriteIds.has(c.id)}" @click="toggleFavorite(c.id)">{{account.favoriteIds.has(c.id) ? '★ 已收藏' : '☆ 收藏教练'}}</button><button class="plain tiny" @click="openCoach(c.id, c.name)">📅 预登记</button></div></td><td><div class="price">¥{{c.price}}<small>/ 课</small></div><p>{{c.districts.join('、')}}</p><div class="hard-matches"><span v-for="(ok,key) in c.hard_matches" :key="key" :class="{mismatch:!ok}">{{ok?'✓':'×'}} {{key}}</span></div></td><td><div class="score"><b>{{c.score}}</b><span>排序参考分 / 100</span></div><details class="score-details"><summary>查看评分依据</summary><p v-for="(score,key) in c.score_breakdown" :key="key">{{key}}：{{score}}</p><small>参考分用于排序，不代表成功概率。</small></details><div class="reason" v-for="reason in c.reasons" :key="reason.evidence_id"><p>{{reason.text}}</p><blockquote>{{reason.quote}}</blockquote><button class="text-button" @click="source(reason.evidence_id)" :disabled="sendingSource">查看来源 ↗</button></div><p class="muted" v-if="!c.reasons.length">当前未找到可引用的相关资料。</p></td><td><p class="availability">{{slots(c)}}</p><ul class="pending"><li v-for="p in c.pending" :key="p">{{p}}</li></ul></td></tr></tbody></table></div>
                </div>
                <p v-if="run.result.degraded.length" class="degraded">{{run.result.degraded.join('；')}}</p>
              </template>
              <p v-if="run.status==='cancelled'" class="muted">这次任务已取消，可以按新的条件继续。</p>
            </div>
          </article>
        </div>
        <div class="composer-wrap"><form class="composer" @submit.prevent="submit"><textarea v-model="input" :placeholder="chat.current?.status==='waiting_for_user'?'补充条件或选择放宽方案…':'说说目标、预算、区域和时间，或问一个运动问题…'" rows="2" @keydown.enter.exact.prevent="submit"></textarea><div class="composer-bottom"><span>Enter 发送 · Shift + Enter 换行</span><div><button v-if="chat.busy" type="button" class="plain" @click="chat.cancel">停止</button><button type="submit" class="send-button" :disabled="!input.trim() || chat.posting" aria-label="发送消息">发送 ↑</button></div></div></form><p class="composer-note">相关训练经验不等同于医疗资质。你的明确条件只有在你同意后才会调整。</p></div>
      </section>
    </main>
    <div v-if="opened" class="modal-backdrop" @click.self="opened=null"><section class="source-modal" role="dialog" aria-modal="true" aria-label="来源原文"><button class="close-button" @click="opened=null">×</button><div class="eyebrow">资料原文 · {{opened.is_demo?'虚构演示资料':'当前有效版本'}}</div><h2>{{opened.source_title}}</h2><div class="source-meta">{{opened.source}} · 版本 {{opened.version}}</div><h3>{{opened.title}}</h3><pre>{{opened.text}}</pre></section></div>
    <CoachDetail v-if="detailCoach" :coach-id="detailCoach.id" :fallback-name="detailCoach.name" :session-id="chat.session?.id" @close="detailCoach=null" />
    <AccountPanel v-if="accountOpen" :username="auth.user.username" @close="accountOpen=false" @open-coach="(id, name) => {accountOpen=false; openCoach(id, name)}" />
  </div>
  <ReportView v-if="report" :report="report" @close="report=null" />
  <KnowledgeBase v-if="kbOpen" @close="kbOpen=false" />
</template>
