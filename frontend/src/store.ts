import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export type Evidence = { id: string; title: string; text: string; source: string; source_title: string; version: string; coach_id: string | null; is_demo: boolean }
export type Requirements = { goal?: string; budget_max?: number | null; district?: string | null; delivery_mode?: string | null; available_windows?: {days:number[];start:number;end:number}[]; preferences?: string[]; self_reported_conditions?: string[]; version?:number }
export type Candidate = { id:string;name:string;price:number;districts:string[];specialties:string[];availability:{days:number[];start:number;end:number}[];is_demo:boolean;score:number;score_breakdown:Record<string,number>;hard_matches:Record<string,boolean>;pending:string[];reasons:{text:string;evidence_id:string;quote:string}[];evidence_ids:string[];delivery_modes:string[] }
export type Run = { id:string;status:string;message:string;version:number;error?:string;requirements:Requirements;clarification?:{id:string;question:string;options:string[]};result?:{mode:string;summary:string;notice:string;degraded:string[];intents?:string[];candidates:Candidate[];evidence:Evidence[];knowledge?:{answer:string;citations:string[];evidence:Evidence[];source_type:string};preregistration?:Preregistration|null} }
export type CoachProfile = { id:string;name:string;price:number;districts:string[];delivery_modes:string[];specialties:string[];qualifications:unknown[];courses:unknown[];availability:{days:number[];start:number;end:number}[];bio:string;is_demo:boolean }
export type ScheduleDay = { date:string;weekday:number;label:string;is_available:boolean;windows:{start:number;end:number}[];registrations:{date:string;start:number;end:number;id:string;status:string;status_label:string}[] }
export type CoachSchedule = { coach:{id:string;name:string;price:number;districts:string[];delivery_modes:string[];specialties:string[];availability:{days:number[];start:number;end:number}[];is_demo:boolean};timezone:string;start_date:string;end_date:string;days:ScheduleDay[] }
export type Preregistration = { id:string;coach_id:string;session_id?:string|null;slots:{date:string;start:number;end:number}[];note:string;status:string;status_label:string;created_at:string;coach?:{id:string;name:string;price:number}|null }
export type Favorites = { favorited_at:string;coach:CoachProfile }
export type Profile = { goals:{value:string;count:number}[];districts:{value:string;count:number}[];delivery_modes:{value:string;count:number}[];preferences:{value:string;count:number}[];self_reported_conditions:{value:string;count:number}[];budget:{min:number;max:number}|null;sessions:number;requirement_versions:number;favorite_coaches:{id:string;name:string}[];preregistered_coaches:{id:string;name:string}[];reset_at?:string|null;updated_at?:string }
export type RunReport = { run_id:string;session_id:string;username:string|null;generated_at:string;mode:string;status:string;message:string;version:number;intents?:string[];summary:string;notice:string;degraded:string[];knowledge_version?:number;requirements:{goal?:string;budget_max?:number|null;district?:string|null;delivery_mode?:string|null;available_windows:{days:number[];start:number;end:number}[];preferences:string[];self_reported_conditions:string[]};candidates:{id:string;name:string;price:number;districts:string[];delivery_modes:string[];specialties:string[];is_demo:boolean;availability:{days:number[];start:number;end:number}[];score:number;score_breakdown:Record<string,number>;score_label:string;hard_matches:Record<string,boolean>;reasons:{text:string;quote:string;evidence_id:string;source_title?:string;source?:string}[];pending:string[]}[];knowledge:{answer:string;source_type:string;citations:string[]}|null;preregistration:Preregistration|null;evidence:{id:string;title:string;source_title:string;source:string;version:string;coach_id:string|null;kind:string}[] }
type Session = {id:string;requirements:Requirements;active_run_id:string|null;runs:Run[]}
export type SessionSummary = {id:string;title:string;created_at:string;updated_at:string}
type ProgressEvent = {id:number;kind:string;data:{message:string;agent?:string;tool?:string;duration_ms?:number;[key:string]:unknown}}
export type User = {id:string;username:string;created_at:string}
export type UserDoc = { id:string;title:string;filename:string;file_type:string;byte_size:number;version:string;chunks:number;created_at:string;updated_at:string }
export type UserKbState = { user_id:string;revision:number;index_revision:number;index_fingerprint:string;importing:boolean }
export type UserEvidence = { id:string;title:string;text:string;source:string;source_title:string;file_type:string;version:string;owner_id:string;truncated?:boolean }
export type UserAnswer = { answer:string;citations:string[];evidence:UserEvidence[];source_type:string;knowledge_version:number }
const base = import.meta.env.VITE_API_BASE_URL || ''

let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(handler: (() => void) | null) { onUnauthorized = handler }

export async function api<T>(path:string, options:RequestInit = {}):Promise<T> {
  const response = await fetch(base + '/api' + path, { ...options, headers: {'Content-Type':'application/json', ...options.headers} })
  const data = await response.json().catch(() => ({}))
  if (response.status === 401 && !path.startsWith('/auth/')) onUnauthorized?.()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '输入格式有误，请检查条件。')
  return data
}

export const useAuth = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const checked = ref(false)
  const pending = ref(false)

  async function check() {
    try { user.value = (await api<{user:User}>('/auth/me')).user }
    catch { user.value = null }
    finally { checked.value = true }
  }

  async function login(username:string, password:string) {
    user.value = (await api<{user:User}>('/auth/login', {method:'POST', body:JSON.stringify({username, password})})).user
  }

  async function register(username:string, password:string) {
    user.value = (await api<{user:User}>('/auth/register', {method:'POST', body:JSON.stringify({username, password})})).user
  }

  async function logout() {
    try { await api('/auth/logout', {method:'POST'}) } finally { user.value = null }
  }

  return {user, checked, pending, check, login, register, logout}
})

const summaryOf = (s:Session):SessionSummary => {
  const first = s.runs?.[0]?.message || ''
  return {id:s.id, title:first.length > 20 ? first.slice(0,20) + '…' : (first || '新对话'),
          created_at:'', updated_at:new Date().toISOString()}
}
const normalize = (s:Session):Session => ({...s, runs:s.runs || []})

export const useChat = defineStore('chat', () => {
  const session = ref<Session | null>(null)
  const sessions = ref<SessionSummary[]>([])
  const health = ref<{mode:string;database:boolean;redis:boolean;model_configured:boolean} | null>(null)
  const error = ref('')
  const progress = ref<ProgressEvent[]>([])
  const posting = ref(false)
  let stream:EventSource | null = null
  let timer:ReturnType<typeof setInterval> | null = null
  let watched = ''
  let generation = 0
  let refreshing = false
  const current = computed(() => session.value?.runs.find(r => r.id === session.value?.active_run_id))
  const busy = computed(() => posting.value || ['created','running'].includes(current.value?.status || ''))

  function disconnect() {
    generation++
    stream?.close(); stream = null
    if (timer) clearInterval(timer)
    timer = null; watched = ''
  }

  function syncEntry() {
    if (!session.value) return
    const entry = sessions.value.find(x => x.id === session.value?.id)
    if (entry) Object.assign(entry, summaryOf(session.value))
    sessions.value.sort((a,b) => b.updated_at.localeCompare(a.updated_at))
  }

  async function reload() {
    if (!session.value) return
    const id = session.value.id
    const next = await api<Session>('/sessions/' + id)
    if (session.value?.id === id) { session.value = normalize(next); syncEntry() }
  }

  async function loadSessions() {
    sessions.value = await api<SessionSummary[]>('/sessions')
  }

  async function openSession(id:string) {
    disconnect(); progress.value = []; error.value = ''
    session.value = normalize(await api<Session>('/sessions/' + id))
    if (current.value && ['created','running'].includes(current.value.status)) watchRun(current.value.id)
  }

  async function initialize() {
    error.value = ''
    try {
      health.value = await api('/health')
      await loadSessions()
      if (sessions.value.length) await openSession(sessions.value[0].id)
      else await newSession()
    } catch (e) { error.value = (e as Error).message + ' 请确认后端已启动。' }
  }

  async function switchSession(id:string) {
    if (session.value?.id === id || posting.value) return
    try { await openSession(id) } catch (e) { error.value = (e as Error).message }
  }

  async function newSession() {
    if (current.value && ['created','running','waiting_for_user'].includes(current.value.status)) await api('/runs/' + current.value.id + '/cancel', {method:'POST'})
    disconnect(); progress.value = []; error.value = ''
    session.value = normalize(await api<Session>('/sessions', {method:'POST'}))
    sessions.value.unshift(summaryOf(session.value))
  }

  async function removeSession(id:string) {
    if (!confirm('删除这次对话？聊天记录将无法恢复。')) return
    try {
      await api('/sessions/' + id, {method:'DELETE'})
      sessions.value = sessions.value.filter(s => s.id !== id)
      if (session.value?.id === id) {
        disconnect(); progress.value = []; session.value = null
        if (sessions.value.length) await openSession(sessions.value[0].id)
        else await newSession()
      }
    } catch (e) { error.value = (e as Error).message }
  }

  function reset() {
    disconnect(); progress.value = []; error.value = ''
    session.value = null; sessions.value = []; health.value = null
  }

  function watchRun(id:string) {
    disconnect(); watched = id
    const thisGeneration = generation
    progress.value = []
    let cursor = 0
    stream = new EventSource(base + '/api/runs/' + id + '/events')
    stream.onmessage = event => {
      if (generation !== thisGeneration) return
      const value = JSON.parse(event.data) as ProgressEvent
      if (value.id <= cursor) return
      cursor = value.id
      progress.value.push(value)
    }
    const refresh = async () => {
      if (refreshing || generation !== thisGeneration) return
      refreshing = true
      try {
        const run = await api<Run>('/runs/' + id)
        if (generation !== thisGeneration) return
        const index = session.value?.runs.findIndex(r => r.id === id) ?? -1
        if (session.value && index >= 0) {
          session.value.runs[index] = run
          session.value.requirements = run.requirements
        }
        if (!['created','running'].includes(run.status)) {
          disconnect()
          await reload()
        }
      } catch (e) { error.value = (e as Error).message }
      finally { refreshing = false }
    }
    stream.addEventListener('done', refresh)
    // 轮询作为断线降级，刷新后仍可用 SQL 状态恢复。
    timer = setInterval(refresh, 1500)
    void refresh()
  }

  async function send(content:string, patch?:Record<string,unknown>, coach_ids:string[] = []) {
    if (!session.value || posting.value) return
    posting.value = true; error.value = ''
    try {
      const run = await api<Run>('/sessions/' + session.value.id + '/messages', {method:'POST',body:JSON.stringify({content,patch,coach_ids})})
      await reload()
      watchRun(run.id)
    } catch (e) { error.value = (e as Error).message }
    finally { posting.value = false }
  }

  async function reply(answer:string) {
    const request = current.value?.clarification
    if (!request || posting.value) return
    posting.value = true; error.value = ''
    try {
      const run = await api<Run>('/clarifications/' + request.id + '/reply', {method:'POST',body:JSON.stringify({answer})})
      await reload()
      if (['created','running'].includes(run.status)) watchRun(run.id)
    } catch (e) { error.value = (e as Error).message }
    finally { posting.value = false }
  }

  async function cancel() {
    if (!current.value) return
    try { await api('/runs/' + current.value.id + '/cancel', {method:'POST'}); disconnect(); await reload() }
    catch(e) { error.value = (e as Error).message }
  }

  return {session,sessions,health,error,progress,posting,current,busy,initialize,newSession,switchSession,removeSession,reset,loadSessions,send,reply,cancel,disconnect}
})

export const useAccount = defineStore('account', () => {
  const profile = ref<Profile | null>(null)
  const favorites = ref<Favorites[]>([])
  const preregistrations = ref<Preregistration[]>([])
  const error = ref('')
  const loading = ref(false)

  const favoriteIds = computed(() => new Set(favorites.value.map(f => f.coach.id)))

  async function load() {
    loading.value = true; error.value = ''
    try {
      const [p, f, r] = await Promise.all([
        api<Profile>('/me/profile'),
        api<Favorites[]>('/me/favorites'),
        api<Preregistration[]>('/me/preregistrations'),
      ])
      profile.value = p; favorites.value = f; preregistrations.value = r
    } catch (e) { error.value = (e as Error).message }
    finally { loading.value = false }
  }

  function reset() { profile.value = null; favorites.value = []; preregistrations.value = []; error.value = '' }

  async function toggleFavorite(coachId:string) {
    try {
      if (favoriteIds.value.has(coachId)) await api('/me/favorites/' + coachId, {method:'DELETE'})
      else await api('/me/favorites', {method:'POST', body:JSON.stringify({coach_id:coachId})})
      await load()
    } catch (e) { error.value = (e as Error).message }
  }

  async function preregister(coachId:string, slots:{date:string;start:number;end:number}[], note = '', sessionId?:string) {
    error.value = ''
    try {
      const record = await api<Preregistration>('/me/preregistrations', {method:'POST',
        body:JSON.stringify({coach_id:coachId, slots, note, session_id: sessionId || null})})
      await load()
      return record
    } catch (e) { error.value = (e as Error).message; throw e }
  }

  async function schedule(coachId:string) {
    return await api<CoachSchedule>('/coaches/' + coachId + '/schedule')
  }

  async function removePreregistration(id:string) {
    error.value = ''
    try { await api('/me/preregistrations/' + id, {method:'DELETE'}); await load() }
    catch (e) { error.value = (e as Error).message; throw e }
  }

  async function clearPreregistrations() {
    error.value = ''
    try { await api('/me/preregistrations', {method:'DELETE'}); await load() }
    catch (e) { error.value = (e as Error).message; throw e }
  }

  async function resetProfile() {
    error.value = ''
    try { await api('/me/profile', {method:'DELETE'}); await load() }
    catch (e) { error.value = (e as Error).message; throw e }
  }

  return {profile,favorites,preregistrations,error,loading,favoriteIds,load,reset,toggleFavorite,preregister,schedule,removePreregistration,clearPreregistrations,resetProfile}
})

export const useKnowledge = defineStore('knowledge', () => {
  const documents = ref<UserDoc[]>([])
  const state = ref<UserKbState | null>(null)
  const error = ref('')
  const uploading = ref(false)
  const asking = ref(false)
  const answer = ref<UserAnswer | null>(null)

  async function load() {
    error.value = ''
    try {
      const data = await api<{documents:UserDoc[];state:UserKbState}>('/kb/documents')
      documents.value = data.documents; state.value = data.state
    } catch (e) { error.value = (e as Error).message }
  }

  function reset() { documents.value = []; state.value = null; answer.value = null; error.value = '' }

  async function upload(file:File) {
    uploading.value = true; error.value = ''
    try {
      const form = new FormData()
      form.append('file', file)
      // 注意：不能设置 Content-Type，浏览器需要自动带上 multipart 边界。
      const response = await fetch(base + '/api/kb/documents', {method:'POST', body:form, credentials:'same-origin'})
      const data = await response.json().catch(() => ({}))
      if (response.status === 401) onUnauthorized?.()
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '上传失败')
      await load()
      return data as {document:UserDoc;parents:number;index:{chunks:number}}
    } catch (e) { error.value = (e as Error).message; throw e }
    finally { uploading.value = false }
  }

  async function removeDocument(id:string) {
    error.value = ''
    try { await api('/kb/documents/' + id, {method:'DELETE'}); await load() }
    catch (e) { error.value = (e as Error).message; throw e }
  }

  async function reindex() {
    error.value = ''
    try { const r = await api<{revision:number;chunks:number;mode:string}>('/kb/reindex', {method:'POST'}); await load(); return r }
    catch (e) { error.value = (e as Error).message; throw e }
  }

  async function ask(question:string) {
    asking.value = true; error.value = ''; answer.value = null
    try { answer.value = await api<UserAnswer>('/kb/query', {method:'POST', body:JSON.stringify({question})}) }
    catch (e) { error.value = (e as Error).message }
    finally { asking.value = false }
  }

  async function evidence(id:string, version?:string) {
    return await api<UserEvidence>('/kb/evidence/' + id + '?version=' + encodeURIComponent(version || ''))
  }

  return {documents,state,error,uploading,asking,answer,load,reset,upload,removeDocument,reindex,ask,evidence}
})
