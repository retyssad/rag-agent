<script setup lang="ts">
import { computed, ref } from 'vue'
import { useAccount } from './store'

defineProps<{ username:string }>()
const emit = defineEmits<{ (e:'close'):void; (e:'openCoach', coachId:string, name:string):void }>()
const account = useAccount()
const tab = ref<'profile' | 'favorites' | 'preregistrations'>('profile')
const busy = ref(false)

const profile = computed(() => account.profile)
const weekdayNames = ['周一','周二','周三','周四','周五','周六','周日']
function slotWeekday(date:string) {
  const jsDay = new Date(date + 'T00:00:00').getDay()
  return weekdayNames[jsDay === 0 ? 6 : jsDay - 1]
}
const topValues = (rows:{value:string;count:number}[]) =>
  rows.length ? rows.map(r => `${r.value}（${r.count}）`).join('、') : '暂无'

async function removeOne(r:{id:string;coach?:{name:string}|null}) {
  if (!confirm(`删除这条预登记（${r.coach?.name || '教练'}）？`)) return
  busy.value = true
  try { await account.removePreregistration(r.id) }
  catch { /* 错误已在 store 中记录 */ }
  finally { busy.value = false }
}
async function clearAll() {
  if (!confirm(`清空全部 ${account.preregistrations.length} 条预登记？此操作不可恢复。`)) return
  busy.value = true
  try { await account.clearPreregistrations() }
  catch { /* 同上 */ }
  finally { busy.value = false }
}
async function clearProfile() {
  if (!confirm('清空跨会话画像？聊天记录会保留，但画像将重新从新的会话开始累积。')) return
  busy.value = true
  try { await account.resetProfile() }
  catch { /* 同上 */ }
  finally { busy.value = false }
}
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <section class="account-modal" role="dialog" aria-modal="true" aria-label="我的账号">
      <button class="close-button" @click="emit('close')">×</button>
      <div class="eyebrow">我的账号</div>
      <h2>👤 {{ username }}</h2>
      <p class="muted">这里汇总你的跨会话画像、收藏教练与预登记时间安排。</p>

      <div class="account-tabs">
        <button :class="{active:tab==='profile'}" @click="tab='profile'">跨会话画像</button>
        <button :class="{active:tab==='favorites'}" @click="tab='favorites'">收藏教练 {{ account.favorites.length }}</button>
        <button :class="{active:tab==='preregistrations'}" @click="tab='preregistrations'">预登记 {{ account.preregistrations.length }}</button>
      </div>

      <p v-if="account.error" class="inline-note error">{{ account.error }}</p>

      <div v-if="tab==='profile'" class="account-body">
        <p class="muted" v-if="!profile">正在汇总…</p>
        <template v-else>
          <div class="panel-toolbar">
            <p class="muted" v-if="profile.reset_at">已清空过，画像只统计 {{ profile.reset_at.slice(0,10) }} 之后的新会话。</p>
            <span v-else></span>
            <button class="plain tiny danger" :disabled="busy" @click="clearProfile">清空画像</button>
          </div>
          <dl class="profile-list">
            <div><dt>训练目标</dt><dd>{{ topValues(profile.goals) }}</dd></div>
            <div><dt>区域偏好</dt><dd>{{ topValues(profile.districts) }}</dd></div>
            <div><dt>上课方式</dt><dd>{{ topValues(profile.delivery_modes) }}</dd></div>
            <div><dt>预算范围</dt><dd>{{ profile.budget ? `¥${profile.budget.min} ~ ¥${profile.budget.max}` : '暂无' }}</dd></div>
            <div><dt>教学偏好</dt><dd>{{ topValues(profile.preferences) }}</dd></div>
            <div><dt>自述情况</dt><dd>{{ topValues(profile.self_reported_conditions) }}</dd></div>
            <div><dt>会话统计</dt><dd>{{ profile.sessions }} 次会话 · {{ profile.requirement_versions }} 条需求版本</dd></div>
          </dl>
          <p class="profile-note">画像只用于让建议更贴合你，不会自动成为硬条件，也不会自动放宽或过滤候选。清空只重置画像累积，聊天记录会保留。</p>
        </template>
      </div>

      <div v-else-if="tab==='favorites'" class="account-body">
        <p class="muted" v-if="!account.favorites.length">还没有收藏教练。在候选表或教练详情里点「收藏教练」即可。</p>
        <ul class="favorite-list">
          <li v-for="f in account.favorites" :key="f.coach.id">
            <button class="favorite-open" @click="emit('openCoach', f.coach.id, f.coach.name)">
              <strong>{{ f.coach.name }}</strong>
              <span class="muted">¥{{ f.coach.price }} / 课 · {{ f.coach.districts.join('、') }}</span>
            </button>
            <button class="text-button" @click="account.toggleFavorite(f.coach.id)">移除</button>
          </li>
        </ul>
      </div>

      <div v-else class="account-body">
        <p class="muted" v-if="!account.preregistrations.length">还没有预登记。可在教练详情中按未来四周可约时段登记。</p>
        <template v-else>
          <div class="panel-toolbar">
            <span class="muted">{{ account.preregistrations.length }} 条预登记</span>
            <button class="plain tiny danger" :disabled="busy" @click="clearAll">清空全部预登记</button>
          </div>
          <ul class="preregister-list">
            <li v-for="r in account.preregistrations" :key="r.id">
              <div class="prereg-head">
                <strong>{{ r.coach?.name }}</strong>
                <span class="prereg-head-right">
                  <span class="reg-badge">{{ r.status_label }}</span>
                  <button class="item-delete" :aria-label="'删除这条预登记'" title="删除这条预登记" @click="removeOne(r)">×</button>
                </span>
              </div>
              <ul><li v-for="s in r.slots" :key="s.date + s.start">
                {{ s.date }}（{{ slotWeekday(s.date) }}）{{ s.start }}:00–{{ s.end }}:00
              </li></ul>
              <p v-if="r.note" class="muted">备注：{{ r.note }}</p>
            </li>
          </ul>
        </template>
      </div>
    </section>
  </div>
</template>
