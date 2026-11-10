<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import Icon from './Icon.vue'
import { api } from '../api'
import type {
  WebSearchDirective, WebSearchPreview, WebSearchRunResult, WebSearchStatus,
  WebArchiveEntry, WebArchiveStats,
} from '../types'

const props = defineProps<{ defaultSchool?: string }>()
const emit = defineEmits<{ (e: 'changed'): void }>()

const fallbackTopics = [
  { id: 'leadership_speech', label: '领导讲话', category: 'SPEECH', terms: '' },
  { id: 'strategic_plan', label: '战略规划', category: 'REFERENCE', terms: '' },
  { id: 'school_profile', label: '办学定位', category: 'REFERENCE', terms: '' },
  { id: 'talent_training', label: '人才培养', category: 'REFERENCE', terms: '' },
  { id: 'quality_report', label: '质量报告', category: 'REFERENCE', terms: '' },
  { id: 'discipline_reform', label: '学科与改革', category: 'REFERENCE', terms: '' },
]

const status = ref<WebSearchStatus | null>(null)
const archive = ref<WebArchiveEntry[]>([])
const stats = ref<WebArchiveStats | null>(null)
const preview = ref<WebSearchPreview | null>(null)
const result = ref<WebSearchRunResult | null>(null)
const busy = ref('')
const message = ref('')
const error = ref('')
const expanded = ref<WebArchiveEntry | null>(null)

const form = reactive({
  school: props.defaultSchool || '',
  site: '',
  keywords: '',
  topics: ['leadership_speech', 'strategic_plan'] as string[],
  maxResults: 5,
  maxPages: 8,
  yearFrom: null as number | null,
  yearTo: null as number | null,
  includeUndated: true,
  refresh: false,
  fetchContent: true,
})

/** 允许填 25 或 2025；前端先归一化，后端也会再校验一次。 */
function toFullYear(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const n = Number(value)
  if (!Number.isFinite(n)) return null
  const year = Math.trunc(n)
  if (year >= 1 && year <= 99) return 2000 + year
  return year >= 2000 && year <= 2100 ? year : null
}

const yearError = computed(() => {
  const from = toFullYear(form.yearFrom)
  const to = toFullYear(form.yearTo)
  return from && to && from > to ? '起始年份不能大于结束年份' : ''
})

const topics = computed(() => status.value?.topics?.length ? status.value.topics : fallbackTopics)
const keySourceLabel = computed(() => ({
  'model-config': '模型设置中的 DeepSeek Key',
  environment: '环境变量专用密钥',
  none: '未配置',
}[status.value?.keySource || 'none'] || '未配置'))

function directive(): WebSearchDirective {
  return {
    enabled: true,
    school: form.school.trim(),
    site: form.site.trim(),
    topics: [...form.topics],
    keywords: form.keywords.trim(),
    maxResults: Number(form.maxResults) || 5,
    maxPages: Number(form.maxPages) || 8,
    yearFrom: toFullYear(form.yearFrom),
    yearTo: toFullYear(form.yearTo),
    includeUndated: form.includeUndated,
    refresh: form.refresh,
    fetchContent: form.fetchContent,
  }
}
function toggleTopic(id: string) {
  const index = form.topics.indexOf(id)
  if (index >= 0) {
    if (form.topics.length > 1) form.topics.splice(index, 1)
  } else if (form.topics.length < 6) form.topics.push(id)
}
function failure(e: unknown) {
  error.value = e instanceof Error ? e.message : '操作失败，请稍后重试'
}
function resetNotes() { message.value = ''; error.value = '' }

async function loadAll() {
  busy.value = 'load'
  resetNotes()
  try {
    const [nextStatus, list] = await Promise.all([api.webSearchStatus(), api.webArchive()])
    status.value = nextStatus
    archive.value = list.entries
    stats.value = list.stats
  } catch (e) { failure(e) } finally { busy.value = '' }
}
async function doPreview() {
  resetNotes()
  if (yearError.value) { error.value = yearError.value; return }
  busy.value = 'preview'
  try { preview.value = await api.webSearchPreview(directive()) } catch (e) { failure(e) } finally { busy.value = '' }
}
async function doRun() {
  resetNotes()
  if (yearError.value) { error.value = yearError.value; return }
  busy.value = 'run'
  try {
    const outcome = await api.webSearchRun(directive())
    result.value = outcome
    preview.value = { school: form.school, site: form.site,
      maxResults: Number(form.maxResults) || 5, maxPages: Number(form.maxPages) || 8,
      yearFrom: outcome.yearFrom ?? toFullYear(form.yearFrom),
      yearTo: outcome.yearTo ?? toFullYear(form.yearTo),
      yearLabel: outcome.yearLabel || '', includeUndated: form.includeUndated,
      refresh: form.refresh, queries: outcome.queries || [] }
    const filtered = outcome.filteredYears ? `，按年份过滤 ${outcome.filteredYears} 页` : ''
    message.value = `检索完成：归档 ${outcome.entries.length} 页，复用 ${outcome.reused ?? 0} 页${filtered}，新增入库 ${outcome.imported} 份资料`
    await loadAll()
    if (outcome.imported) emit('changed')
  } catch (e) { failure(e) } finally { busy.value = '' }
}
async function doSync() {
  busy.value = 'sync'; resetNotes()
  try {
    const outcome = await api.webArchiveSync()
    message.value = `已把 ${outcome.imported} 份归档资料登记进资料库`
    await loadAll()
    if (outcome.imported) emit('changed')
  } catch (e) { failure(e) } finally { busy.value = '' }
}
async function doTest() {
  busy.value = 'test'; resetNotes()
  try {
    const outcome = await api.webSearchTest()
    message.value = `联网搜索连接正常（返回 ${outcome.results} 条结果，本次产生一次按次计费请求）`
  } catch (e) { failure(e) } finally { busy.value = '' }
}
async function doDelete(id: string) {
  if (!window.confirm('确定删除这条归档记录？已登记到资料库的文件不会被删除。')) return
  busy.value = 'delete'; resetNotes()
  try {
    await api.webArchiveDelete(id)
    if (expanded.value?.id === id) expanded.value = null
    await loadAll()
    message.value = '归档记录已删除'
  } catch (e) { failure(e) } finally { busy.value = '' }
}
async function viewEntry(entry: WebArchiveEntry) {
  busy.value = 'view'; resetNotes()
  try { expanded.value = await api.webArchiveEntry(entry.id) } catch (e) { failure(e) } finally { busy.value = '' }
}
function formatTime(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

watch(() => props.defaultSchool, (value) => { if (value && !form.school) form.school = value })
onMounted(loadAll)
</script>

<template>
  <div class="web-search">
    <div class="page-heading">
      <div>
        <div class="eyebrow">WEB RESEARCH</div>
        <h1>联网检索</h1>
        <p>让智能体自动访问学校官网，抓取领导讲话、战略规划等公开信息，并归档进资料库复用。</p>
      </div>
      <button class="button" :disabled="!!busy" @click="loadAll"><Icon name="RefreshCw" :size="16" />刷新</button>
    </div>

    <p v-if="message" class="ws-note ok">{{ message }}</p>
    <p v-if="error" class="ws-note bad">{{ error }}</p>

    <div class="ws-grid">
      <section class="panel">
        <div class="panel-heading">
          <div class="section-heading">
            <span class="section-icon"><Icon name="Search" :size="19" /></span>
            <div><h3>定向检索指令</h3><p>指定学校、官网域名与主题，系统自动构造 site: 检索式</p></div>
          </div>
        </div>
        <div class="ws-fields">
          <label class="field"><span>学校名称 <b>*</b></span>
            <input v-model="form.school" maxlength="100" placeholder="例如：北京信息科技大学" /></label>
          <label class="field"><span>学校官网域名</span>
            <input v-model="form.site" maxlength="200" placeholder="例如：bistu.edu.cn" /></label>
          <label class="field full"><span>补充关键词</span>
            <input v-model="form.keywords" maxlength="200" placeholder="可留空；例如：2025 人才培养工作会议" /></label>
        </div>
        <div class="ws-topics">
          <span class="ws-label">检索主题（可多选）</span>
          <div class="ws-chips">
            <button v-for="topic in topics" :key="topic.id" type="button"
              :class="['ws-chip', { selected: form.topics.includes(topic.id) }]"
              @click="toggleTopic(topic.id)">
              {{ topic.label }}<small>{{ topic.category }}</small>
            </button>
          </div>
        </div>
        <div class="ws-fields compact">
          <label class="field"><span>每主题结果数</span>
            <input v-model.number="form.maxResults" type="number" min="1" max="10" /></label>
          <label class="field"><span>最多归档页数</span>
            <input v-model.number="form.maxPages" type="number" min="1" max="30" /></label>
          <label class="field"><span>资料起始年份</span>
            <input v-model.number="form.yearFrom" type="number" min="1" max="2100" placeholder="不限" /></label>
          <label class="field"><span>资料结束年份</span>
            <input v-model.number="form.yearTo" type="number" min="1" max="2100" placeholder="不限" /></label>
        </div>
        <p class="ws-hint" style="margin: 10px 0 0">
          填 <code>25</code> 或 <code>2025</code> 都表示 2025 年；起止填同一年（如 25 → 25）即只获取该年资料。
          年份作为检索关键词参与排序，<strong>不使用时间过滤</strong>：能判定年份但落在区间外的页面会被排除，官网大量不标日期的页面默认放行并单独统计。
        </p>
        <div class="ws-options">
          <label><input v-model="form.includeUndated" type="checkbox" /> 包含未标注年份的页面（推荐；取消勾选可严格排除）</label>
          <label><input v-model="form.refresh" type="checkbox" /> 忽略归档缓存，重新联网抓取</label>
          <label><input v-model="form.fetchContent" type="checkbox" /> 摘要过短时补抓网页正文</label>
        </div>
        <p v-if="yearError" class="ws-note bad" style="margin-top: 10px">{{ yearError }}</p>
        <div class="ws-actions">
          <button class="button" :disabled="!!busy || !form.school.trim()" @click="doPreview">
            <Icon name="Eye" :size="16" />{{ busy === 'preview' ? '生成中…' : '预览检索指令' }}</button>
          <button class="button primary" :disabled="!!busy || !form.school.trim() || !form.topics.length" @click="doRun">
            <Icon name="Globe" :size="16" />{{ busy === 'run' ? '检索中…' : '执行检索并入库' }}</button>
        </div>
      </section>

      <section class="panel">
        <div class="panel-heading">
          <div class="section-heading">
            <span class="section-icon"><Icon name="Wifi" :size="19" /></span>
            <div><h3>服务状态</h3><p>DeepSeek 官方 Search / Fetch</p></div>
          </div>
          <span :class="['muted-badge', status?.configured ? 'ready' : '']">
            {{ status?.configured ? '已配置' : '未配置' }}</span>
        </div>
        <dl class="ws-status-list">
          <div><dt>密钥来源</dt><dd>{{ keySourceLabel }}</dd></div>
          <div><dt>接口地址</dt><dd class="mono">{{ status?.baseUrl || '—' }}</dd></div>
          <div><dt>归档缓存</dt><dd>{{ status?.cacheDays ?? '—' }} 天内相同指令直接复用</dd></div>
          <div><dt>归档总量</dt><dd>{{ stats?.total ?? 0 }} 页，其中 {{ stats?.pending ?? 0 }} 页待入库</dd></div>
          <div><dt>任务内自动检索</dt><dd>{{ status?.enabled ? '已开启' : '按任务指令开启' }}</dd></div>
        </dl>
        <p class="ws-hint">{{ status?.hint || '联网搜索按成功请求次数单独计费，搜索无结果不计费。' }}</p>
        <div class="ws-actions">
          <button class="button" :disabled="!!busy || !status?.configured" @click="doTest">
            {{ busy === 'test' ? '测试中…' : '测试连接' }}</button>
          <button class="button" :disabled="!!busy" @click="doSync">
            {{ busy === 'sync' ? '同步中…' : '同步归档到资料库' }}</button>
        </div>
        <p class="ws-hint">抓取到的网页会以 .txt 资料登记到资料库；报告工作台可直接勾选复用，无需人工预先收集。</p>
      </section>
    </div>

    <section v-if="preview" class="panel">
      <div class="panel-heading"><div class="section-heading">
        <span class="section-icon"><Icon name="Terminal" :size="19" /></span>
        <div><h3>展开后的搜索指令</h3><p>每条指令对应一个主题，命中归档缓存时不再联网</p></div>
      </div></div>
      <p v-if="preview.yearLabel" class="ws-hint">
        限定资料年份：<strong>{{ preview.yearLabel }}</strong>
        <template v-if="preview.includeUndated">（未标注年份的页面会放行并在结果中标注）</template>
        <template v-else>（严格模式：未标注年份的页面会被排除）</template>
      </p>
      <p v-if="preview.yearHint" class="ws-hint">{{ preview.yearHint }}</p>
      <div class="ws-queries">
        <div v-for="item in preview.queries" :key="item.queryKey" class="ws-query">
          <span class="ws-query-label">{{ item.label }}<small>{{ item.category }}</small></span>
          <code>{{ item.query }}</code>
        </div>
        <p v-if="!preview.queries.length" class="ws-hint">本次没有生成检索指令。</p>
      </div>
    </section>

    <section v-if="result" class="panel">
      <div class="panel-heading"><div class="section-heading">
        <span class="section-icon"><Icon name="FileDown" :size="19" /></span>
        <div><h3>本次检索结果</h3><p>共 {{ result.entries.length }} 页，复用 {{ result.reused ?? 0 }} 页，新增入库 {{ result.imported }} 份<span v-if="result.filteredYears">，按年份过滤 {{ result.filteredYears }} 页</span></p></div>
      </div></div>
      <p v-if="result.yearLabel" class="ws-hint">限定资料年份：{{ result.yearLabel }}；只有该区间的网页会归档入库，入库资料抬头与文件名已标注年份。</p>
      <p v-for="(issue, index) in result.errors || []" :key="index" class="ws-note bad">
        {{ issue.topic || issue.code }}：{{ issue.message }}
      </p>
      <div class="ws-results">
        <article v-for="entry in result.entries" :key="entry.id" class="ws-result">
          <div><strong>{{ entry.title }}</strong><span class="ws-host">{{ entry.host }}</span></div>
          <p>{{ entry.snippet || '（无摘要）' }}</p>
          <div class="ws-tags">
            <span>{{ entry.topicLabel }}</span><span>{{ entry.category }}</span>
            <span v-if="entry.yearLabel">{{ entry.yearLabel }} 年资料</span>
            <span>{{ entry.contentChars }} 字</span>
            <span v-if="entry.reused">归档复用</span>
            <span :class="entry.materialId ? 'done' : ''">{{ entry.materialId ? '已入库' : '待入库' }}</span>
          </div>
        </article>
        <p v-if="!result.entries.length" class="ws-hint">没有获取到符合条件的官网页面，可放宽域名或调整主题后重试。</p>
      </div>
    </section>

    <section class="panel">
      <div class="panel-heading"><div class="section-heading">
        <span class="section-icon"><Icon name="Archive" :size="19" /></span>
        <div><h3>官网资料归档库</h3><p>按用户隔离，同一网址只保留一份正文；可离线复用</p></div>
      </div></div>
      <div class="table-scroll">
        <table v-if="archive.length">
          <thead><tr><th>标题</th><th>学校 / 主题</th><th>抓取时间</th><th>入库状态</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="entry in archive" :key="entry.id">
              <td><div class="table-file"><strong>{{ entry.title }}</strong><span>{{ entry.host }}</span></div></td>
              <td>{{ entry.school }}<span class="ws-sub">{{ entry.topicLabel }}</span></td>
              <td>{{ formatTime(entry.fetchedAt) }}</td>
              <td><span :class="['ws-state', entry.materialId ? 'done' : '']">{{ entry.materialId ? '已入库' : '待入库' }}</span></td>
              <td><div class="ws-row-actions">
                <button class="text-button" :disabled="!!busy" @click="viewEntry(entry)">查看</button>
                <a class="text-button" :href="entry.url" target="_blank" rel="noopener noreferrer">原网页</a>
                <button class="text-button danger" :disabled="!!busy" @click="doDelete(entry.id)">删除</button>
              </div></td>
            </tr>
          </tbody>
        </table>
        <p v-else class="ws-hint">归档库还是空的。先填写学校与官网域名，执行一次检索即可自动入库。</p>
      </div>
    </section>

    <section v-if="expanded" class="panel">
      <div class="panel-heading"><div class="section-heading">
        <span class="section-icon"><Icon name="FileText" :size="19" /></span>
        <div><h3>{{ expanded.title }}</h3><p>{{ expanded.url }}</p></div>
      </div><button class="text-button" @click="expanded = null">收起</button></div>
      <pre class="ws-content">{{ expanded.content }}</pre>
    </section>
  </div>
</template>

<style scoped>
.web-search { display: flex; flex-direction: column; gap: 18px; }
.ws-grid { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr); gap: 18px; }
.ws-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px; margin-top: 16px; }
.ws-fields.compact { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.ws-fields .full { grid-column: 1 / -1; }
.ws-topics { margin-top: 16px; }
.ws-label { display: block; margin-bottom: 8px; color: var(--text-secondary); font-size: 13px; }
.ws-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.ws-chip { display: inline-flex; align-items: center; gap: 6px; padding: 6px 11px; border: 1px solid var(--border-strong);
  border-radius: 999px; background: var(--surface); color: var(--text-secondary); cursor: pointer; font-size: 13px; }
.ws-chip small { color: var(--muted); font-size: 11px; }
.ws-chip.selected { border-color: var(--primary); background: var(--primary-soft); color: var(--primary); }
.ws-options { display: flex; flex-wrap: wrap; gap: 18px; margin-top: 16px; color: var(--text-secondary); font-size: 13px; }
.ws-options label { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
.ws-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }
.ws-status-list { margin: 14px 0 0; display: flex; flex-direction: column; gap: 10px; }
.ws-status-list > div { display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
.ws-status-list dt { color: var(--muted); font-size: 13px; }
.ws-status-list dd { margin: 0; text-align: right; font-size: 13px; color: var(--text); }
.mono { font-family: var(--font-mono, monospace); font-size: 12px !important; word-break: break-all; }
.ws-hint { color: var(--muted); font-size: 12px; line-height: 1.6; margin: 12px 0 0; }
.muted-badge.ready { color: var(--success); border-color: var(--success-border); background: var(--success-soft); }
.ws-note { padding: 10px 14px; border-radius: 8px; font-size: 13px; margin: 0; }
.ws-note.ok { color: var(--success); background: var(--success-soft); border: 1px solid var(--success-border); }
.ws-note.bad { color: var(--danger); background: #fdf2f0; border: 1px solid var(--danger-border); }
.ws-queries { display: flex; flex-direction: column; gap: 10px; margin-top: 14px; }
.ws-query { display: flex; align-items: center; gap: 14px; background: var(--surface-soft); border: 1px solid var(--border);
  border-radius: 8px; padding: 10px 14px; }
.ws-query-label { min-width: 100px; font-size: 13px; color: var(--text); }
.ws-query-label small { display: block; color: var(--muted); font-size: 11px; }
.ws-query code { font-size: 12px; color: var(--text-secondary); word-break: break-all; }
.ws-results { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; margin-top: 14px; }
.ws-result { border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; background: var(--surface); }
.ws-result strong { font-size: 13px; color: var(--text); }
.ws-host { display: block; color: var(--muted); font-size: 11px; margin-top: 3px; }
.ws-result p { color: var(--text-secondary); font-size: 12px; line-height: 1.6; margin: 8px 0;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.ws-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.ws-tags span { font-size: 11px; color: var(--muted); background: var(--surface-muted); border-radius: 4px; padding: 2px 7px; }
.ws-tags span.done { color: var(--success); background: var(--success-soft); }
.ws-state { font-size: 12px; color: var(--warning); }
.ws-state.done { color: var(--success); }
.ws-sub { display: block; color: var(--muted); font-size: 11px; }
.ws-row-actions { display: flex; gap: 10px; align-items: center; }
.text-button.danger { color: var(--danger); }
.ws-content { max-height: 420px; overflow: auto; white-space: pre-wrap; word-break: break-word; font-size: 12px;
  line-height: 1.7; background: var(--surface-soft); border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
@media (max-width: 1080px) { .ws-grid { grid-template-columns: 1fr; } .ws-fields.compact { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 640px) { .ws-fields, .ws-fields.compact { grid-template-columns: 1fr; } }
</style>
