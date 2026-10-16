<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useKnowledge, type UserEvidence } from './store'

const emit = defineEmits<{ (e:'close'):void }>()
const kb = useKnowledge()
const question = ref('')
const opened = ref<UserEvidence | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const notice = ref('')

function humanSize(bytes:number) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + ' KB'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}
function citationIndex(id:string) {
  const list = kb.answer?.citations || []
  return list.indexOf(id)
}
async function pick(event:Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  notice.value = ''
  try {
    const result = await kb.upload(file)
    notice.value = `已入库《${result.document.title}》，生成 ${result.parents} 个段落，索引 ${result.index.chunks} 个片段。`
  } catch { /* 错误已在 store 中记录 */ }
  finally { if (fileInput.value) fileInput.value.value = '' }
}
async function remove(id:string, title:string) {
  if (!confirm(`删除《${title}》？该文档会从你的知识库移除。`)) return
  notice.value = ''
  try { await kb.removeDocument(id); notice.value = '已删除该文档。' } catch { /* 同上 */ }
}
async function rebuild() {
  notice.value = ''
  try { const r = await kb.reindex(); notice.value = `索引已重建，共 ${r.chunks} 个片段。` }
  catch { /* 同上 */ }
}
async function ask() {
  const value = question.value.trim()
  if (!value) return
  notice.value = ''
  await kb.ask(value)
}
async function open(id:string, version?:string) {
  try { opened.value = await kb.evidence(id, version) } catch { /* 同上 */ }
}
onMounted(() => { kb.reset(); kb.load(); notice.value = '' })
</script>

<template>
  <div class="kb-screen">
    <div class="kb-head">
      <div>
        <div class="eyebrow">我的知识库 · 仅本账号可见</div>
        <h1>上传你的健身与补剂资料，基于原文问答。</h1>
        <p class="muted">支持 PDF 与 Markdown（.md/.txt）。检索方式与主界面一致：父子块、多路召回、RRF 融合、独立重排，回答带原文引用。</p>
      </div>
      <div class="kb-head-actions">
        <button class="plain" @click="emit('close')">返回对话</button>
      </div>
    </div>

    <p v-if="kb.error" class="error-banner" role="alert">{{ kb.error }}</p>
    <p v-else-if="notice" class="inline-note">{{ notice }}</p>

    <div class="kb-grid">
      <section class="kb-panel">
        <div class="kb-panel-head">
          <h2>我的文档 <small v-if="kb.state">v{{ kb.state.revision }}</small></h2>
          <div class="kb-panel-actions">
            <button class="plain tiny" :disabled="!kb.documents.length || kb.uploading" @click="rebuild">重建索引</button>
            <button class="primary tiny" :disabled="kb.uploading" @click="fileInput?.click()">
              {{ kb.uploading ? '处理中…' : '上传 PDF / Markdown' }}
            </button>
            <input ref="fileInput" type="file" accept=".pdf,.md,.markdown,.txt" class="hidden-file" @change="pick">
          </div>
        </div>

        <p v-if="kb.state?.importing" class="muted">索引正在重建，期间问答会暂停。</p>
        <p v-if="!kb.documents.length" class="muted">
          还没有文档。上传后即可基于原文提问，例如“肌酸怎么吃”“每天需要多少蛋白质”。
        </p>
        <ul v-else class="kb-docs">
          <li v-for="doc in kb.documents" :key="doc.id">
            <span class="kb-type" :class="doc.file_type">{{ doc.file_type.toUpperCase() }}</span>
            <div class="kb-doc-body">
              <strong>{{ doc.title }}</strong>
              <span class="muted">{{ doc.filename }} · {{ humanSize(doc.byte_size) }} · {{ doc.chunks }} 个片段</span>
            </div>
            <button class="item-delete" :aria-label="'删除文档'" title="删除文档" @click="remove(doc.id, doc.title)">×</button>
          </li>
        </ul>
      </section>

      <section class="kb-panel kb-ask">
        <div class="kb-panel-head"><h2>基于我的文档提问</h2></div>
        <form class="kb-ask-form" @submit.prevent="ask">
          <textarea v-model="question" rows="2" placeholder="例如：一水肌酸冲击法和维持量分别是多少？"
                    @keydown.enter.exact.prevent="ask"></textarea>
          <button class="primary" type="submit" :disabled="kb.asking || !question.trim()">
            {{ kb.asking ? '检索中…' : '提问' }}
          </button>
        </form>

        <div v-if="kb.answer" class="kb-answer">
          <div class="answer-text">{{ kb.answer.answer }}</div>
          <div class="source-row">
            <span>{{ kb.answer.source_type === 'user_documents' ? '依据你上传的资料' : '无依据' }}</span>
            <button v-for="id in kb.answer.citations" :key="id" @click="open(id)">
              来源 {{ citationIndex(id) + 1 }} ↗
            </button>
            <span v-if="!kb.answer.citations.length" class="muted">没有可引用的原文</span>
          </div>
        </div>
        <p v-else class="muted">回答只使用你自己上传的文档，不会混入通用教练或平台资料。</p>
      </section>
    </div>

    <div v-if="opened" class="modal-backdrop" @click.self="opened=null">
      <section class="source-modal" role="dialog" aria-modal="true" aria-label="你的文档原文">
        <button class="close-button" @click="opened=null">×</button>
        <div class="eyebrow">你的文档原文 · {{ opened.file_type.toUpperCase() }}</div>
        <h2>{{ opened.source_title }}</h2>
        <div class="source-meta">{{ opened.source }}</div>
        <h3>{{ opened.title }}</h3>
        <pre>{{ opened.text }}</pre>
      </section>
    </div>
  </div>
</template>
