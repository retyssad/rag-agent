<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, useAccount, type CoachProfile, type CoachSchedule } from './store'

const props = defineProps<{ coachId:string; fallbackName:string; sessionId?:string }>()
const emit = defineEmits<{ (e:'close'):void }>()
const account = useAccount()
const schedule = ref<CoachSchedule | null>(null)
const coach = ref<CoachProfile | null>(null)
const selected = ref<string[]>([])
const note = ref('')
const message = ref('')
const busy = ref(false)

const isFavorite = computed(() => account.favoriteIds.has(props.coachId))
const slotKey = (date:string, start:number) => date + '#' + start
function hours(day:{windows:{start:number;end:number}[]}) {
  const result:number[] = []
  for (const w of day.windows) for (let h = w.start; h < w.end; h++) result.push(h)
  return result
}
function toggle(date:string, start:number) {
  const key = slotKey(date, start)
  selected.value = selected.value.includes(key) ? selected.value.filter(k => k !== key) : [...selected.value, key]
}
function slotsToSubmit() {
  return selected.value.map(key => {
    const [date, start] = key.split('#')
    return { date, start: Number(start), end: Number(start) + 1 }
  })
}

async function load() {
  message.value = ''
  try {
    schedule.value = await account.schedule(props.coachId)
    coach.value = await api<CoachProfile>('/coaches/' + props.coachId)
  } catch (e) { message.value = (e as Error).message }
}

watch(() => props.coachId, () => { selected.value = []; load() }, { immediate: true })

async function save() {
  if (!selected.value.length) { message.value = '请先选择要登记的时段。'; return }
  busy.value = true
  try {
    const record = await account.preregister(props.coachId, slotsToSubmit(), note.value, props.sessionId)
    message.value = `已登记 · 待教练确认（${record.slots.length} 个时段）`
    selected.value = []; note.value = ''
    schedule.value = await account.schedule(props.coachId)
  } catch (e) { message.value = (e as Error).message }
  finally { busy.value = false }
}
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <section class="coach-modal" role="dialog" aria-modal="true" :aria-label="'教练详情：' + fallbackName">
      <button class="close-button" @click="emit('close')">×</button>
      <div class="eyebrow">教练详情与未来四周时间安排</div>
      <div class="coach-modal-head">
        <h2>{{ schedule?.coach.name || fallbackName }}</h2>
        <button class="plain" :class="{faved:isFavorite}" @click="account.toggleFavorite(coachId)">
          {{ isFavorite ? '★ 已收藏' : '☆ 收藏教练' }}
        </button>
      </div>
      <p class="muted" v-if="schedule">
        ¥{{ schedule.coach.price }} / 课 · {{ schedule.coach.districts.join('、') }} ·
        {{ schedule.coach.delivery_modes.join(' / ') }} · {{ schedule.coach.specialties.join('、') }}
      </p>
      <p class="muted" v-if="schedule?.coach.is_demo">虚构演示教练资料。</p>
      <p v-if="coach?.bio" class="coach-bio">{{ coach.bio }}</p>

      <div v-if="message" class="inline-note">{{ message }}</div>

      <template v-if="schedule">
        <div class="schedule-legend">
          <span><i class="dot avail"></i>可约</span>
          <span><i class="dot sel"></i>已选</span>
          <span><i class="dot reg"></i>已登记 · 待教练确认</span>
          <span class="muted">{{ schedule.start_date }} ~ {{ schedule.end_date }}（香港时间）</span>
        </div>
        <div class="schedule-grid">
          <div v-for="day in schedule.days" :key="day.date"
               class="schedule-cell" :class="{open:day.is_available, taken:day.registrations.length}">
            <div class="schedule-date">{{ day.date.slice(5) }}<small>{{ day.label }}</small></div>
            <template v-if="day.registrations.length">
              <span v-for="r in day.registrations" :key="r.id" class="reg-badge" :title="r.status_label">
                已登记 {{ r.start }}:00–{{ r.end }}:00
              </span>
            </template>
            <template v-else-if="day.is_available">
              <button v-for="h in hours(day)" :key="h" class="slot-chip"
                      :class="{picked:selected.includes(slotKey(day.date, h))}"
                      @click="toggle(day.date, h)">
                {{ h }}:00–{{ h + 1 }}:00
              </button>
            </template>
            <span v-else class="muted">休息</span>
          </div>
        </div>
        <div class="preregister-form">
          <label>备注（可选）<input v-model="note" maxlength="200" placeholder="例如：想先确认膝盖不适能否训练"></label>
          <button class="primary" :disabled="busy || !selected.length" @click="save">
            提交预登记（{{ selected.length }}）
          </button>
        </div>
      </template>
      <p v-else class="muted">正在读取时间安排…</p>
    </section>
  </div>
</template>
