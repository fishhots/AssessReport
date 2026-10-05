<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import Icon from './components/Icon.vue'
import DiagnosisFramework from './components/DiagnosisFramework.vue'
import ModelSettingsDialog from './components/ModelSettingsDialog.vue'
import { diagnosisDimensions, plannedOutputs } from './diagnosisFramework'
import { api } from './api'
import type { Catalog, Category, Material, ModelConfig, ModelOption, ReportTask } from './types'

type Page = 'workspace' | 'diagnosis' | 'materials' | 'reports' | 'models'
const nav = [
  { id: 'workspace', label: '报告工作台', icon: 'LayoutDashboard' },
  { id: 'diagnosis', label: '个性化报告', icon: 'ClipboardList' },
  { id: 'materials', label: '资料库', icon: 'FolderOpen' },
  { id: 'reports', label: '报告记录', icon: 'FileText' },
  { id: 'models', label: '模型设置', icon: 'SlidersHorizontal' },
] as const
const categories: { id: Category; label: string; icon: string }[] = [
  { id: 'DATA', label: '调研数据', icon: 'FileSpreadsheet' },
  { id: 'SPEECH', label: '发展讲话', icon: 'MessageSquareText' },
  { id: 'REFERENCE', label: '参考报告', icon: 'BookOpen' },
]
const page = ref<Page>('workspace')
const mobileNav = ref(false)
const catalog = ref<Catalog | null>(null)
const materials = ref<Material[]>([])
const reports = ref<ReportTask[]>([])
const loading = ref(true)
const loadError = ref('')
const uploadBusy = ref(false)
const saving = ref(false)
const running = ref(false)
const stoppingId = ref('')
const deletingReport = ref(false)
const deletedReportIds = new Set<string>()
const downloading = ref('')
const testingModel = ref('')
const modelTestResults = ref<Record<string, string>>({})
const editingModel = ref<ModelOption | null>(null)
let pollTimer: ReturnType<typeof setTimeout>
const dragActive = ref(false)
const category = ref<Category>('DATA')
const filterCategory = ref('ALL')
const search = ref('')
const fileInput = ref<HTMLInputElement>()
const selectedIds = ref<string[]>([])
const school = ref('北京信息科技大学')
const year = ref(2025)
const templateId = 'graduate'
const modelId = ref('qwen')
const chapters = ref<string[]>([])
const instructions = ref('')
const customTopicEnabled = ref(false)
const customTopicTitle = ref('')
const customTopicRequirements = ref('')
const activeReport = ref<ReportTask | null>(null)
const isLegacyReport = (task: ReportTask) => task.status === 'SUCCEEDED' && task.steps.some((step) => step.name === '深度诊断')
const activeOutputs = computed(() => activeReport.value && isLegacyReport(activeReport.value)
  ? [{ id: 'report', name: '毕业生培养质量评价报告', icon: 'FileText', description: '历史任务输出' },
     { id: 'diagnosis', name: '深度诊断框架', icon: 'ClipboardList', description: '历史任务输出' }]
  : plannedOutputs)
const helpOpen = ref(false)
const pendingDelete = ref<Material | null>(null)
const pendingReportDelete = ref<ReportTask | null>(null)
const toast = ref<{ message: string; error: boolean } | null>(null)
let toastTimer: ReturnType<typeof setTimeout>
const activeTemplate = computed(() => catalog.value?.templates.find((t) => t.id === templateId))
const allChaptersSelected = computed(
  () =>
    !!activeTemplate.value?.chapters.length && chapters.value.length === activeTemplate.value.chapters.length,
)
const activeModel = computed(() => catalog.value?.models.find((m) => m.id === modelId.value))
const selectedMaterials = computed(() => materials.value.filter((m) => selectedIds.value.includes(m.id)))
const filteredMaterials = computed(() =>
  materials.value.filter(
    (m) =>
      (filterCategory.value === 'ALL' || m.category === filterCategory.value) &&
      m.name.toLowerCase().includes(search.value.toLowerCase()),
  ),
)
const filteredReports = computed(() =>
  reports.value.filter((r) => r.title.toLowerCase().includes(search.value.toLowerCase())),
)
const ready = computed(
  () =>
    !!school.value.trim() &&
    Number.isInteger(year.value) &&
    year.value >= 2000 &&
    year.value <= 2100 &&
    selectedIds.value.length > 0 &&
    chapters.value.length > 0 &&
    !!activeTemplate.value &&
    (!customTopicEnabled.value || (!!customTopicTitle.value.trim() && !!customTopicRequirements.value.trim())) &&
    !!activeModel.value,
)
const pageTitle = computed(() => nav.find((n) => n.id === page.value)?.label)
const totalSize = computed(() => materials.value.reduce((sum, m) => sum + m.size, 0))
const categoryName = (id: string) => categories.find((c) => c.id === id)?.label || id
const modelName = (id: string) => catalog.value?.models.find((m) => m.id === id)?.name || id
const statusLabel = (status: string) =>
  ({
    DRAFT: '待生成',
    AWAITING_AGENT: '待生成',
    QUEUED: '排队中',
    RUNNING: '生成中',
    CANCELLING: '正在终止',
    CANCELLED: '已终止',
    SUCCEEDED: '已完成',
    FAILED: '生成失败',
    INTERRUPTED: '已中断',
  })[status] || '待处理'
const stepLabel = (status: string) =>
  ({ COMPLETED: '已完成', RUNNING: '进行中', CANCELLING: '正在终止', CANCELLED: '已终止', FAILED: '失败', INTERRUPTED: '已中断', WAITING: '待执行' })[
    status
  ] || '待执行'
const isActiveTask = (task: ReportTask) => ['QUEUED', 'RUNNING', 'CANCELLING'].includes(task.status)
const sizeLabel = (size: number) =>
  size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.ceil(size / 1024))} KB`
const dateLabel = (value: string) =>
  new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value))
const fileIcon = (name: string) => (/\.(xlsx?|csv)$/i.test(name) ? 'FileSpreadsheet' : 'FileText')
function notify(message: string, error = false) {
  clearTimeout(toastTimer)
  toast.value = { message, error }
  toastTimer = setTimeout(() => (toast.value = null), 4500)
}
function navigate(id: Page) {
  page.value = id
  search.value = ''
  mobileNav.value = false
  void nextTick(() => window.scrollTo(0, 0))
}
function toggleMaterial(id: string) {
  selectedIds.value = selectedIds.value.includes(id)
    ? selectedIds.value.filter((x) => x !== id)
    : [...selectedIds.value, id]
}
function toggleChapter(name: string) {
  const selected = new Set(chapters.value)
  if (selected.has(name)) selected.delete(name)
  else selected.add(name)
  chapters.value = (activeTemplate.value?.chapters || []).filter((chapter) => selected.has(chapter))
}
function toggleAllChapters() {
  chapters.value = allChaptersSelected.value ? [] : [...(activeTemplate.value?.chapters || [])]
}
async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const [nextCatalog, nextMaterials, nextReports] = await Promise.all([
      api.catalog(),
      api.materials(),
      api.reports(),
    ])
    catalog.value = nextCatalog
    materials.value = nextMaterials
    reports.value = nextReports
    try {
      const raw = localStorage.getItem('assess-report-draft')
      if (raw) {
        const draft = JSON.parse(raw)
        if (typeof draft.school === 'string') school.value = draft.school
        if (Number.isInteger(draft.year)) year.value = draft.year
        if (nextCatalog.models.some((m) => m.id === draft.modelId)) modelId.value = draft.modelId
        if (typeof draft.instructions === 'string') instructions.value = draft.instructions
        customTopicEnabled.value = draft.customTopicEnabled === true
        if (typeof draft.customTopicTitle === 'string') customTopicTitle.value = draft.customTopicTitle
        if (typeof draft.customTopicRequirements === 'string') customTopicRequirements.value = draft.customTopicRequirements
        if (Array.isArray(draft.selectedIds))
          selectedIds.value = draft.selectedIds.filter((id: string) => nextMaterials.some((m) => m.id === id))
        chapters.value =
          draft.chapterSchema === activeTemplate.value?.chapters.join('|') && Array.isArray(draft.chapters)
            ? (activeTemplate.value?.chapters || []).filter((name) => draft.chapters.includes(name))
            : [...(activeTemplate.value?.chapters || [])]
      } else chapters.value = [...(activeTemplate.value?.chapters || [])]
    } catch {
      chapters.value = [...(activeTemplate.value?.chapters || [])]
    }
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '无法连接服务'
  } finally {
    loading.value = false
  }
}
function saveDraft(showToast = true) {
  try {
    localStorage.setItem(
      'assess-report-draft',
      JSON.stringify({
        school: school.value,
        year: year.value,
        templateId,
        chapterSchema: activeTemplate.value?.chapters.join('|'),
        modelId: modelId.value,
        chapters: chapters.value,
        instructions: instructions.value,
        customTopicEnabled: customTopicEnabled.value,
        customTopicTitle: customTopicTitle.value,
        customTopicRequirements: customTopicRequirements.value,
        selectedIds: selectedIds.value,
      }),
    )
    if (showToast) notify('草稿已保存到当前浏览器')
  } catch {
    if (showToast) notify('当前浏览器无法保存草稿', true)
  }
}
watch(
  [school, year, modelId, chapters, instructions, selectedIds, customTopicEnabled, customTopicTitle, customTopicRequirements],
  () => {
    if (!loading.value && catalog.value) saveDraft(false)
  },
  { deep: true },
)
async function upload(files: File[]) {
  dragActive.value = false
  if (!files.length || uploadBusy.value) return
  if (files.length > 20) return notify('每次最多上传 20 个文件', true)
  if (files.reduce((total, f) => total + f.size, 0) > 200 * 1024 * 1024)
    return notify('单次上传总量不能超过 200 MB', true)
  if (files.some((f) => f.size === 0 || f.size > 50 * 1024 * 1024))
    return notify('请上传非空文件，单个文件不超过 50 MB', true)
  if (files.some((f) => !/\.(xlsx?|csv|pdf|docx?|txt)$/i.test(f.name)))
    return notify('支持 Excel、CSV、PDF、Word 和 TXT 文件', true)
  uploadBusy.value = true
  try {
    const added = await api.upload(files, category.value)
    materials.value = [...added, ...materials.value]
    selectedIds.value = [...selectedIds.value, ...added.map((m) => m.id)]
    notify(`已上传 ${added.length} 份资料`)
  } catch (e) {
    notify(e instanceof Error ? e.message : '上传失败', true)
  } finally {
    uploadBusy.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}
function onFiles(event: Event) {
  void upload(Array.from((event.target as HTMLInputElement).files || []))
}
function onDrop(event: DragEvent) {
  void upload(Array.from(event.dataTransfer?.files || []))
}
async function deleteMaterial() {
  if (!pendingDelete.value) return
  const id = pendingDelete.value.id
  try {
    await api.deleteMaterial(id)
    materials.value = materials.value.filter((m) => m.id !== id)
    selectedIds.value = selectedIds.value.filter((x) => x !== id)
    notify('资料已删除')
  } catch (e) {
    notify(e instanceof Error ? e.message : '删除失败', true)
  } finally {
    pendingDelete.value = null
  }
}
async function createReport() {
  if (!ready.value || saving.value || uploadBusy.value) return
  saving.value = true
  try {
    const report = await api.createReport({
      school: school.value.trim(),
      year: year.value,
      templateId,
      modelId: modelId.value,
      materialIds: selectedIds.value,
      chapters: chapters.value,
      instructions: instructions.value,
      customTopic: customTopicEnabled.value ? { title: customTopicTitle.value.trim(), requirements: customTopicRequirements.value.trim() } : null,
    })
    reports.value = [report, ...reports.value]
    activeReport.value = report
    notify('报告任务已创建，配置与资料已保存')
  } catch (e) {
    notify(e instanceof Error ? e.message : '任务创建失败', true)
  } finally {
    saving.value = false
  }
}
function chooseModelFromSettings(id: string) {
  modelId.value = id
  navigate('workspace')
}
async function runReport() {
  if (!activeReport.value || running.value) return
  running.value = true
  try {
    const report = await api.runReport(activeReport.value.id)
    activeReport.value = report
    reports.value = reports.value.map((r) => (r.id === report.id ? report : r))
    notify('任务已提交')
  } catch (e) {
    notify(e instanceof Error ? e.message : '任务启动失败', true)
  } finally {
    running.value = false
  }
}
async function downloadOutput(kind: string, name: string) {
  if (!activeReport.value || downloading.value) return
  downloading.value = kind
  try {
    const task = activeReport.value
    await api.downloadOutput(task.id, kind, `${task.school}${task.year}届${name}.docx`)
  } catch (e) {
    notify(e instanceof Error ? e.message : '下载失败', true)
  } finally {
    downloading.value = ''
  }
}
async function cancelReport(task: ReportTask) {
  if (stoppingId.value || task.status === 'CANCELLING') return
  stoppingId.value = task.id
  try {
    const updated = await api.cancelReport(task.id)
    reports.value = reports.value.map((r) => (r.id === updated.id ? updated : r))
    if (activeReport.value?.id === updated.id) activeReport.value = updated
    notify(updated.status === 'CANCELLING' ? '正在终止，当前请求结束后停止任务' : '任务已终止')
  } catch (e) {
    notify(e instanceof Error ? e.message : '终止失败', true)
  } finally {
    stoppingId.value = ''
  }
}
function confirmReportDelete(task: ReportTask) {
  if (isActiveTask(task) || running.value) return
  activeReport.value = null
  pendingReportDelete.value = task
}
async function deleteReport() {
  if (!pendingReportDelete.value || deletingReport.value) return
  const id = pendingReportDelete.value.id
  deletingReport.value = true
  try {
    await api.deleteReport(id)
    deletedReportIds.add(id)
    reports.value = reports.value.filter((r) => r.id !== id)
    if (activeReport.value?.id === id) activeReport.value = null
    pendingReportDelete.value = null
    notify('报告记录已删除，上传资料已保留')
  } catch (e) {
    notify(e instanceof Error ? e.message : '删除失败', true)
  } finally {
    deletingReport.value = false
  }
}
async function testModel(id: string) {
  if (testingModel.value) return
  testingModel.value = id
  try {
    await api.testModel(id)
    modelTestResults.value[id] = '接口测试通过'
    notify(`${modelName(id)} 接口测试通过`)
  } catch (e) {
    modelTestResults.value[id] = '接口测试失败'
    notify(e instanceof Error ? e.message : '模型测试失败', true)
  } finally {
    testingModel.value = ''
  }
}
function openModelSettings(model: ModelOption) {
  if (!testingModel.value) editingModel.value = model
}
function modelConfigSaved(config: ModelConfig) {
  const model = catalog.value?.models.find((item) => item.id === config.id)
  if (model) model.configured = config.configured
  delete modelTestResults.value[config.id]
}
function modelConfigTested(id: string, passed: boolean) {
  modelTestResults.value[id] = passed ? '接口测试通过' : '接口测试失败'
}
async function pollReports() {
  try {
    if (reports.value.some(isActiveTask)) {
      reports.value = (await api.reports()).filter((r) => !deletedReportIds.has(r.id))
      if (activeReport.value)
        activeReport.value = reports.value.find((r) => r.id === activeReport.value?.id) || activeReport.value
    }
  } catch {
    /* Keep existing progress until the next successful refresh. */
  }
  pollTimer = setTimeout(pollReports, 4000)
}
function closeDialogs() {
  activeReport.value = null
  helpOpen.value = false
  pendingDelete.value = null
  pendingReportDelete.value = null
}
function showReportHistory() {
  activeReport.value = null
  navigate('reports')
}
onMounted(async () => {
  await load()
  pollTimer = setTimeout(pollReports, 4000)
})
onUnmounted(() => {
  clearTimeout(pollTimer)
  clearTimeout(toastTimer)
})
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar" :class="{ 'mobile-open': mobileNav }">
      <a class="brand" href="#" @click.prevent="navigate('workspace')"
        ><span class="brand-mark"><Icon name="GraduationCap" :size="25" /></span
        ><span class="brand-name">教学质量报告智能体</span></a
      >
      <div class="workspace-label">WORKSPACE <span>工作空间</span></div>
      <nav aria-label="主导航">
        <button
          v-for="item in nav"
          :key="item.id"
          :class="['nav-item', { active: page === item.id }]"
          @click="navigate(item.id)"
        >
          <Icon :name="item.icon" :size="19" /><span>{{ item.label }}</span
          ><span v-if="item.id === 'reports' && reports.length" class="nav-count">{{ reports.length }}</span
          ><span v-if="page === item.id" class="nav-dot"></span>
        </button>
      </nav>
      <div class="sidebar-guide">
        <span class="guide-icon"><Icon name="Sparkles" :size="19" /></span>
        <h4>让数据成为改进的起点</h4>
        <p>汇集校本资料，构建有依据、可追溯的教学质量报告。</p>
        <button @click="helpOpen = true">了解使用流程 <Icon name="ArrowUpRight" :size="16" /></button>
      </div>
      <div class="sidebar-bottom">
        <div class="avatar">教</div>
        <div><strong>教学质量工作空间</strong><span>教学质量评价与改进</span></div>
        <button class="icon-button" aria-label="使用帮助" @click="helpOpen = true">
          <Icon name="HelpCircle" :size="18" />
        </button>
      </div>
    </aside>
    <div v-if="mobileNav" class="nav-backdrop" @click="mobileNav = false"></div>
    <div class="main-shell">
      <header class="topbar">
        <div class="breadcrumb">
          <button class="icon-button mobile-menu" aria-label="展开导航" @click="mobileNav = !mobileNav">
            <Icon name="Menu" /></button
          ><span>工作空间</span><Icon name="ChevronRight" :size="14" /><strong>{{ pageTitle }}</strong>
        </div>
        <div class="topbar-actions">
          <span class="connection"
            ><i :class="{ offline: loadError }"></i
            >{{ loadError ? '服务未连接' : loading ? '连接中' : '本地服务已连接' }}</span
          ><button class="icon-button" aria-label="刷新数据" @click="load">
            <Icon name="RefreshCw" :size="17" /></button
          ><span class="topbar-divider"></span>
          <div class="avatar small">教</div>
        </div>
      </header>
      <main>
        <div v-if="loading" class="load-state">
          <Icon name="LoaderCircle" class="spin" :size="28" />
          <h3>正在准备工作空间</h3>
          <p>读取报告模板与资料库…</p>
        </div>
        <div v-else-if="loadError" class="load-state">
          <Icon name="Info" :size="30" />
          <h3>暂时无法连接后端服务</h3>
          <p>{{ loadError }}。请确认后端服务已启动。</p>
          <button class="button primary" @click="load">重新连接</button>
        </div>
        <template v-else-if="catalog">
          <template v-if="page === 'workspace'">
            <div class="page-heading">
              <div>
                <div class="eyebrow">REPORT STUDIO</div>
                <h1>报告工作台</h1>
                <p>整理校本资料，生成包含个性化分析与主题章节的培养质量评价报告。</p>
              </div>
              <button class="button" @click="saveDraft()">
                <Icon name="ClipboardList" :size="17" />保存草稿
              </button>
            </div>
            <section class="hero-banner">
              <div class="hero-content">
                <span class="hero-label"><span></span>面向高校的教学质量评价</span>
                <h2>读懂培养数据，<br />看见教育的下一步。</h2>
                <p>
                  以校本资料为依据，以报告模板为框架，<br class="desktop-break" />连接多模型分析与持续改进。
                </p>
                <div class="hero-tags">
                  <span><Icon name="Database" :size="14" />校本数据</span
                  ><span><Icon name="Layers" :size="14" />规范模板</span
                  ><span><Icon name="Sparkles" :size="14" />多模型协作</span>
                </div>
              </div>
              <div class="hero-art" aria-hidden="true">
                <div class="orb orb-one"></div>
                <div class="orb orb-two"></div>
                <div class="art-grid"></div>
                <div class="art-card back-card"><span></span><span></span><span></span></div>
                <div class="art-card front-card">
                  <div class="paper-top">
                    <div class="paper-logo"><Icon name="GraduationCap" :size="20" /></div>
                    <span>QUALITY REPORT</span>
                  </div>
                  <div class="paper-title">教学质量评价</div>
                  <div class="paper-subtitle">以证据支持每一次改进</div>
                  <div class="paper-chart"><i></i><i></i><i></i><i></i><i></i><i></i></div>
                  <div class="paper-line long"></div>
                  <div class="paper-line"></div>
                  <div class="paper-line short"></div>
                  <div class="paper-bottom">
                    <span>培养成效 · 持续改进</span><Icon name="ArrowUpRight" :size="15" />
                  </div>
                </div>
                <div class="art-float"><Icon name="ShieldCheck" :size="18" /><span>从数据到洞察</span></div>
                <div class="sparkle-one">✦</div>
                <div class="sparkle-two">✧</div>
              </div>
            </section>
            <div class="workflow-strip">
              <div class="workflow-item current">
                <span>01</span>
                <div><strong>上传校本资料</strong><small>汇集数据与背景信息</small></div>
              </div>
              <Icon name="ChevronRight" :size="18" />
              <div class="workflow-item">
                <span>02</span>
                <div><strong>配置报告任务</strong><small>勾选章节与分析模型</small></div>
              </div>
              <Icon name="ChevronRight" :size="18" />
              <div class="workflow-item">
                <span>03</span>
                <div><strong>生成完整报告</strong><small>模板章节、个性化分析与主题报告</small></div>
              </div>
            </div>
            <div class="studio-grid">
              <div class="studio-main">
                <section class="panel">
                  <div class="panel-heading">
                    <div class="section-heading">
                      <span class="section-icon"><Icon name="FolderOpen" :size="19" /></span>
                      <div>
                        <h3>准备报告资料</h3>
                        <p>上传数据、发展讲话或参考报告，为分析提供依据</p>
                      </div>
                    </div>
                    <span class="muted-badge">{{ selectedIds.length }} 份已选</span>
                  </div>
                  <div class="category-tabs">
                    <button
                      v-for="c in categories"
                      :key="c.id"
                      :class="{ active: category === c.id }"
                      @click="category = c.id"
                    >
                      <Icon :name="c.icon" :size="15" />{{ c.label }}
                    </button>
                  </div>
                  <button
                    class="drop-zone"
                    :class="{ dragging: dragActive, busy: uploadBusy }"
                    :disabled="uploadBusy"
                    @click="fileInput?.click()"
                    @dragover.prevent="dragActive = true"
                    @dragleave.prevent="dragActive = false"
                    @drop.prevent="onDrop"
                  >
                    <span class="upload-icon"
                      ><Icon
                        :name="uploadBusy ? 'LoaderCircle' : 'UploadCloud'"
                        :class="{ spin: uploadBusy }"
                        :size="29" /></span
                    ><strong>{{ uploadBusy ? '正在上传并保存资料…' : '点击上传，或将文件拖拽到这里' }}</strong
                    ><span>Excel、CSV、PDF、Word、TXT · 单文件不超过 50 MB</span
                    ><span class="upload-category">将保存为「{{ categoryName(category) }}」</span>
                  </button>
                  <div v-if="materials.length" class="selected-files">
                    <div class="file-list-title">
                      <span
                        >可用资料 <b>{{ materials.length }}</b></span
                      ><button
                        class="text-button"
                        @click="
                          selectedIds =
                            selectedIds.length === materials.length ? [] : materials.map((m) => m.id)
                        "
                      >
                        {{ selectedIds.length === materials.length ? '取消全选' : '选择全部' }}
                      </button>
                    </div>
                    <label v-for="m in materials.slice(0, 5)" :key="m.id" class="material-row"
                      ><input
                        type="checkbox"
                        :checked="selectedIds.includes(m.id)"
                        @change="toggleMaterial(m.id)" /><span
                        class="file-type"
                        :class="{ spreadsheet: fileIcon(m.name) === 'FileSpreadsheet' }"
                        ><Icon :name="fileIcon(m.name)" :size="19" /></span
                      ><span class="file-info"
                        ><strong>{{ m.name }}</strong
                        ><small>{{ categoryName(m.category) }} · {{ sizeLabel(m.size) }}</small></span
                      ><Icon
                        v-if="selectedIds.includes(m.id)"
                        name="CheckCircle2"
                        class="accent-text"
                        :size="17" /></label
                    ><button v-if="materials.length > 5" class="text-button" @click="navigate('materials')">
                      在资料库中查看全部 {{ materials.length }} 份资料 <Icon name="ArrowRight" :size="14" />
                    </button>
                  </div>
                  <div v-else class="upload-hint">
                    <Icon name="Info" :size="14" /><span
                      >建议先上传毕业生调研数据，再补充学校发展背景资料。</span
                    >
                  </div>
                </section>
                <section class="panel config-panel">
                  <div class="panel-heading">
                    <div class="section-heading">
                      <span class="section-icon"><Icon name="Settings2" :size="19" /></span>
                      <div>
                        <h3>配置报告内容</h3>
                        <p>填写学校与届别，勾选本次需要输出的报告章节</p>
                      </div>
                    </div>
                  </div>
                  <div class="form-grid">
                    <label class="field"
                      ><span>学校名称 <b>*</b></span
                      ><input v-model="school" maxlength="100" placeholder="请输入学校全称" /></label
                    ><label class="field"
                      ><span>毕业届别 / 评价年度 <b>*</b></span
                      ><input v-model.number="year" type="number" min="2000" max="2100"
                    /></label>
                  </div>
                  <div class="fixed-template">
                    <Icon name="Layers" :size="20" />
                    <div>
                      <strong>{{ activeTemplate?.name }}</strong>
                      <p>{{ activeTemplate?.description }}</p>
                    </div>
                  </div>
                  <div class="field full">
                    <div class="chapter-selection-header">
                      <span>报告章节 <b>*</b><small>默认全选，名称与顺序沿用原报告</small></span>
                      <label class="chapter-select-all"
                        ><input
                          type="checkbox"
                          :checked="allChaptersSelected"
                          :indeterminate="chapters.length > 0 && !allChaptersSelected"
                          @change="toggleAllChapters"
                        /><span>全选</span></label
                      >
                    </div>
                    <div class="chapter-options">
                      <label v-for="chapter in activeTemplate?.chapters" :key="chapter"
                        ><input
                          type="checkbox"
                          :checked="chapters.includes(chapter)"
                          @change="toggleChapter(chapter)"
                        /><span>{{ chapter }}</span></label
                      >
                    </div>
                  </div>
                  <label class="field full"
                    ><span>补充分析要求 <small>选填</small></span
                    ><textarea
                      v-model="instructions"
                      rows="3"
                      maxlength="4000"
                      placeholder="例如：重点分析专业结构与产业需求的匹配情况，结合学校发展目标提出可操作的改进建议。"
                    ></textarea
                    ><span class="character-count">{{ instructions.length }} / 4000</span></label
                  >
                </section>
                <section class="panel diagnosis-preview">
                  <div class="panel-heading">
                    <div class="section-heading">
                      <span class="section-icon"><Icon name="ClipboardList" :size="19" /></span>
                      <div>
                        <h3>第九章 个性化报告</h3>
                      </div>
                    </div>
                  </div>
                  <DiagnosisFramework />
                  <div class="diagnosis-preview-footer">
                    <button class="text-button" @click="navigate('diagnosis')">
                      查看分析要求<Icon name="ArrowUpRight" :size="14" />
                    </button>
                  </div>
                </section>
                <section class="panel custom-topic-panel">
                  <div class="panel-heading">
                    <div class="section-heading"><span class="section-icon"><Icon name="FileText" :size="19" /></span>
                      <div><h3>自定义主题报告</h3><p>在个性化报告后追加主题章节</p></div>
                    </div>
                    <label><input v-model="customTopicEnabled" type="checkbox" /> 添加主题章节</label>
                  </div>
                  <template v-if="customTopicEnabled">
                    <label class="field full"><span>章节题目 <b>*</b></span>
                      <input v-model="customTopicTitle" maxlength="100" placeholder="例如：未来5年适应AI技术的信息类人才培养方向" />
                    </label>
                    <label class="field full"><span>分析要求 <b>*</b></span>
                      <textarea v-model="customTopicRequirements" rows="4" maxlength="4000"
                        placeholder="说明希望回答的问题、分析范围和建议重点。" />
                      <span class="character-count">{{ customTopicRequirements.length }} / 4000</span>
                    </label>
                  </template>
                </section>
                <section class="panel">
                  <div class="panel-heading">
                    <div class="section-heading">
                      <span class="section-icon"><Icon name="Sparkles" :size="19" /></span>
                      <div>
                        <h3>选择分析模型</h3>
                        <p>选择本次报告使用的模型</p>
                      </div>
                    </div>
                    <span class="muted-badge">{{ catalog.models.length }} 类模型</span>
                  </div>
                  <div class="model-grid">
                    <button
                      v-for="model in catalog.models"
                      :key="model.id"
                      :class="['model-card', { selected: modelId === model.id }]"
                      @click="modelId = model.id"
                    >
                      <span :class="['model-logo', model.accent]">{{
                        model.name === 'DeepSeek'
                          ? 'D'
                          : model.name === 'Claude'
                            ? '✳'
                            : model.name === 'Gemini'
                              ? '✦'
                              : model.name === 'Qwen'
                                ? 'Q'
                                : model.name === 'GLM'
                                  ? 'Z'
                                  : 'G'
                      }}</span
                      ><span
                        ><strong>{{ model.name }}</strong
                        ><small>{{ model.provider }}</small></span
                      ><span class="radio-mark"
                        ><Icon v-if="modelId === model.id" name="Check" :size="11"
                      /></span>
                    </button>
                  </div>
                </section>
              </div>
              <aside class="studio-aside">
                <section class="panel summary-panel">
                  <div class="summary-title">
                    <Icon name="ClipboardList" :size="19" />
                    <h3>本次输出</h3>
                    <span class="tiny-dot"></span>
                  </div>
                  <p class="summary-school">{{ school || '待填写学校' }} · {{ year }} 届</p>
                  <div class="summary-outputs">
                    <div v-for="output in plannedOutputs" :key="output.id" class="summary-output-item">
                      <span class="output-file-icon"><Icon :name="output.icon" :size="22" /></span>
                      <div>
                        <strong>{{ output.name }}</strong>
                      </div>
                    </div>
                  </div>
                  <dl class="summary-list">
                    <div>
                      <dt>已选资料</dt>
                      <dd>{{ selectedIds.length }} 份</dd>
                    </div>
                    <div>
                      <dt>报告章节</dt>
                      <dd>{{ chapters.length }} 项模板内容 ＋ {{ customTopicEnabled ? 2 : 1 }} 个新增章节</dd>
                    </div>
                    <div>
                      <dt>个性化分析</dt>
                      <dd>{{ diagnosisDimensions.length }} 个维度</dd>
                    </div>
                    <div>
                      <dt>分析模型</dt>
                      <dd>{{ activeModel?.name }}</dd>
                    </div>
                    <div>
                      <dt>目标格式</dt>
                      <dd><span class="word-badge">W</span> 一份完整 Word 报告</dd>
                    </div>
                  </dl>
                  <div class="summary-requirements">
                    <span
                      ><Icon
                        :name="school.trim() ? 'CheckCircle2' : 'Circle'"
                        :size="15"
                      />评价对象已填写</span
                    ><span :class="{ incomplete: !selectedIds.length }"
                      ><Icon :name="selectedIds.length ? 'CheckCircle2' : 'Circle'" :size="15" />{{
                        selectedIds.length ? '报告资料已选择' : '请上传并选择资料'
                      }}</span
                    ><span :class="{ incomplete: !chapters.length }"
                      ><Icon :name="chapters.length ? 'CheckCircle2' : 'Circle'" :size="15" />{{
                        chapters.length ? '报告范围已配置' : '请至少选择一个章节'
                      }}</span
                    >
                  </div>
                  <button
                    class="button primary create-button"
                    :disabled="!ready || saving || uploadBusy"
                    @click="createReport"
                  >
                    <Icon :name="saving ? 'LoaderCircle' : 'Plus'" :class="{ spin: saving }" :size="18" />{{
                      saving ? '正在创建…' : '创建报告任务'
                    }}<Icon v-if="!saving" name="ArrowRight" :size="17" />
                  </button>
                </section>
              </aside>
            </div>
          </template>
          <template v-else-if="page === 'diagnosis'">
            <div class="page-heading">
              <div>
                <div class="eyebrow">DEEP DIAGNOSIS</div>
                <h1>个性化报告</h1>
                <p>围绕专业建设与人才培养，连接专业定位、学习成果和课程实践体系。</p>
              </div>
              <button class="button" @click="navigate('workspace')">
                <Icon name="LayoutDashboard" :size="17" />返回报告工作台
              </button>
            </div>
            <section class="panel diagnosis-full-panel">
              <div class="panel-heading">
                <div class="section-heading">
                  <span class="section-icon"><Icon name="Search" :size="19" /></span>
                  <div>
                    <h3>诊断维度与分析要求</h3>
                  </div>
                </div>
                <span class="muted-badge">{{ diagnosisDimensions.length }} 个诊断维度</span>
              </div>
              <DiagnosisFramework expanded />
            </section>
            <section class="panel diagnosis-result-panel">
              <div class="section-heading">
                <span class="section-icon"><Icon name="FileText" :size="19" /></span>
                <div>
                  <h3>诊断结论的呈现方式</h3>
                  <p>根据实际资料组织内容，说明判断如何形成以及建议如何落地</p>
                </div>
              </div>
              <div class="diagnosis-result-grid">
                <div>
                  <span>01</span>
                  <h4>问题与判断</h4>
                  <p>指出专业建设或培养体系中的具体问题，区分数据事实与分析判断。</p>
                </div>
                <div>
                  <span>02</span>
                  <h4>数据与依据</h4>
                  <p>说明校本数据、学校发展目标及相关需求资料如何支持诊断。</p>
                </div>
                <div>
                  <span>03</span>
                  <h4>改革建议</h4>
                  <p>给出可执行的专业、学习成果和课程实践改进建议及优先顺序。</p>
                </div>
                <div>
                  <span>04</span>
                  <h4>实施条件与验证</h4>
                  <p>考虑师资、教学资源和办学条件，提出实施条件与效果验证方式。</p>
                </div>
              </div>
            </section>
          </template>
          <template v-else-if="page === 'materials'">
            <div class="page-heading">
              <div>
                <div class="eyebrow">MATERIAL LIBRARY</div>
                <h1>资料库</h1>
                <p>集中管理校本数据与背景资料，让每次分析都有可靠依据。</p>
              </div>
              <button class="button primary" :disabled="uploadBusy" @click="fileInput?.click()">
                <Icon
                  :name="uploadBusy ? 'LoaderCircle' : 'Plus'"
                  :class="{ spin: uploadBusy }"
                  :size="18"
                />上传资料
              </button>
            </div>
            <div class="stat-grid">
              <div class="stat-card">
                <span>资料总数</span><strong>{{ materials.length }}<small>份</small></strong
                ><Icon name="FolderOpen" :size="27" />
              </div>
              <div class="stat-card">
                <span>调研数据</span
                ><strong>{{ materials.filter((m) => m.category === 'DATA').length }}<small>份</small></strong
                ><Icon name="Database" :size="27" />
              </div>
              <div class="stat-card">
                <span>存储用量</span><strong>{{ sizeLabel(totalSize) }}</strong
                ><Icon name="Layers" :size="27" />
              </div>
            </div>
            <section class="panel library-panel">
              <div class="list-toolbar">
                <div class="toolbar-selects">
                  <select v-model="filterCategory" aria-label="筛选资料类别">
                    <option value="ALL">全部资料</option>
                    <option v-for="c in categories" :key="c.id" :value="c.id">{{ c.label }}</option></select
                  ><select v-model="category" aria-label="上传资料类别">
                    <option v-for="c in categories" :key="c.id" :value="c.id">上传为：{{ c.label }}</option>
                  </select>
                </div>
                <label class="search-field"
                  ><Icon name="Search" :size="17" /><input v-model="search" placeholder="搜索资料名称"
                /></label>
              </div>
              <div class="table-scroll">
                <table v-if="filteredMaterials.length">
                  <thead>
                    <tr>
                      <th>用于报告</th>
                      <th>资料名称</th>
                      <th>资料类别</th>
                      <th>文件大小</th>
                      <th>上传时间</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="m in filteredMaterials" :key="m.id">
                      <td>
                        <input
                          type="checkbox"
                          :checked="selectedIds.includes(m.id)"
                          :aria-label="`选择 ${m.name}`"
                          @change="toggleMaterial(m.id)"
                        />
                      </td>
                      <td>
                        <div class="table-file">
                          <span
                            class="file-type"
                            :class="{ spreadsheet: fileIcon(m.name) === 'FileSpreadsheet' }"
                            ><Icon :name="fileIcon(m.name)" :size="20" /></span
                          ><strong>{{ m.name }}</strong>
                        </div>
                      </td>
                      <td>
                        <span class="muted-badge">{{ categoryName(m.category) }}</span>
                      </td>
                      <td>{{ sizeLabel(m.size) }}</td>
                      <td>{{ dateLabel(m.createdAt) }}</td>
                      <td>
                        <div class="row-actions">
                          <a
                            :href="`/api/materials/${m.id}/download`"
                            class="icon-button"
                            :aria-label="`下载 ${m.name}`"
                            ><Icon name="Download" :size="17" /></a
                          ><button
                            class="icon-button"
                            :aria-label="`删除 ${m.name}`"
                            @click="pendingDelete = m"
                          >
                            <Icon name="Trash2" :size="17" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div v-if="!filteredMaterials.length" class="empty-state">
                <span><Icon name="FolderOpen" :size="32" /></span>
                <h3>{{ materials.length ? '没有找到匹配的资料' : '从第一份校本资料开始' }}</h3>
                <p>
                  {{
                    materials.length
                      ? '试试其他关键词或资料类别。'
                      : '上传毕业生调研数据、学校发展讲话或参考报告。'
                  }}
                </p>
                <button
                  v-if="!materials.length"
                  class="button"
                  :disabled="uploadBusy"
                  @click="fileInput?.click()"
                >
                  上传资料<Icon name="ArrowRight" :size="16" />
                </button>
              </div>
              <div class="list-footer">
                共 {{ filteredMaterials.length }} 份资料 · 已选择 {{ selectedIds.length }} 份<button
                  class="text-button"
                  @click="navigate('workspace')"
                >
                  返回工作台配置报告<Icon name="ArrowRight" :size="15" />
                </button>
              </div>
            </section>
          </template>
          <template v-else-if="page === 'reports'">
            <div class="page-heading">
              <div>
                <div class="eyebrow">REPORT HISTORY</div>
                <h1>报告记录</h1>
                <p>每次生成一份培养质量评价报告，包含个性化分析和已填写的主题章节。</p>
              </div>
              <button class="button primary" @click="navigate('workspace')">
                <Icon name="Plus" :size="17" />新建报告
              </button>
            </div>
            <section class="panel library-panel">
              <div class="list-toolbar">
                <div class="table-title">
                  全部报告 <span class="muted-badge">{{ reports.length }}</span>
                </div>
                <label class="search-field"
                  ><Icon name="Search" :size="17" /><input v-model="search" placeholder="搜索报告名称"
                /></label>
              </div>
              <div class="table-scroll">
                <table v-if="filteredReports.length">
                  <thead>
                    <tr>
                      <th>报告名称</th>
                      <th>分析模型</th>
                      <th>资料数量</th>
                      <th>任务状态</th>
                      <th>创建时间</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="r in filteredReports" :key="r.id">
                      <td>
                        <div class="table-file">
                          <span class="file-type"><Icon name="FileText" :size="20" /></span
                          ><span class="record-document"
                            ><strong>{{ r.title }}</strong
                            ><small>{{ isLegacyReport(r) ? '历史报告' : '模板报告＋个性化分析' }}{{ r.customTopic ? '＋主题章节' : '' }}</small></span
                          >
                        </div>
                      </td>
                      <td>{{ modelName(r.modelId) }}</td>
                      <td>{{ r.materialIds.length }} 份</td>
                      <td>
                        <span class="status-badge" :class="{ 'accent-text': r.status === 'SUCCEEDED' }"
                          ><Icon :name="r.status === 'SUCCEEDED' ? 'CheckCircle2' : 'Clock3'" :size="13" />{{
                            statusLabel(r.status)
                          }}</span
                        >
                      </td>
                      <td>{{ dateLabel(r.createdAt) }}</td>
                      <td>
                        <div class="report-actions">
                          <button class="text-button" @click="activeReport = r">查看详情</button>
                          <button v-if="isActiveTask(r)" class="text-button danger-text"
                            :disabled="r.status === 'CANCELLING' || !!stoppingId" @click="cancelReport(r)">
                            {{ r.status === 'CANCELLING' || stoppingId === r.id ? '正在终止…' : '终止' }}
                          </button>
                          <button class="text-button danger-text" :disabled="isActiveTask(r) || running || deletingReport"
                            :title="isActiveTask(r) ? '请先终止任务，等待终止完成后再删除' : '删除报告记录'"
                            @click="confirmReportDelete(r)">删除记录</button>
                        </div>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div v-if="!filteredReports.length" class="empty-state">
                <span><Icon name="FileText" :size="32" /></span>
                <h3>{{ reports.length ? '没有找到匹配的报告' : '你的第一份报告，从这里开始' }}</h3>
                <p>
                  {{
                    reports.length ? '尝试使用其他关键词搜索。' : '上传资料并完成配置后，就可以创建报告任务。'
                  }}
                </p>
                <button v-if="!reports.length" class="button primary" @click="navigate('workspace')">
                  前往报告工作台<Icon name="ArrowRight" :size="16" />
                </button>
              </div>
              <div class="list-footer">
                共 {{ filteredReports.length }} 个报告任务<span>任务配置已保存在本地服务</span>
              </div>
            </section>
          </template>
          <template v-else-if="page === 'models'">
            <div class="page-heading">
              <div>
                <div class="eyebrow">MODEL CONNECTIONS</div>
                <h1>模型设置</h1>
                <p>点击模型，填写密钥、模型名称和接口地址。</p>
              </div>
            </div>
            <div class="provider-grid">
              <article
                v-for="model in catalog.models"
                :key="model.id"
                class="panel provider-card configurable"
                @click="openModelSettings(model)"
              >
                <div>
                  <span :class="['model-logo', model.accent]">{{ model.name.slice(0, 1) }}</span
                  ><span class="muted-badge">{{ model.configured ? '已配置' : '尚未配置' }}</span>
                </div>
                <h3>{{ model.name }}</h3>
                <span>{{ model.provider }}</span>
                <p>{{ model.description }}</p>
                <p v-if="modelTestResults[model.id]" class="model-test-result">
                  {{ modelTestResults[model.id] }}
                </p>
                <button
                  v-if="model.configured"
                  class="text-button model-test-button"
                  :disabled="!!testingModel"
                  @click.stop="testModel(model.id)"
                >
                  {{ testingModel === model.id ? '正在测试…' : '测试连接' }}
                </button>
                <button
                  class="button primary model-config-button"
                  :disabled="!!testingModel"
                  @click.stop="openModelSettings(model)"
                >
                  {{ model.configured ? '编辑配置' : '配置模型' }}<Icon name="SlidersHorizontal" :size="16" />
                </button>
                <button class="button" @click.stop="chooseModelFromSettings(model.id)">
                  在工作台选择<Icon name="ArrowRight" :size="16" />
                </button>
              </article>
            </div>
          </template>
          <footer class="main-footer">
            <span>教学质量报告智能体</span><span>以数据为依据，以改进为方向</span>
          </footer>
        </template>
      </main>
    </div>
    <input
      ref="fileInput"
      class="visually-hidden"
      type="file"
      accept=".xlsx,.xls,.csv,.pdf,.doc,.docx,.txt"
      multiple
      @change="onFiles"
    />
    <ModelSettingsDialog
      v-if="editingModel"
      :key="editingModel.id"
      :provider="editingModel"
      @close="editingModel = null"
      @saved="modelConfigSaved"
      @tested="modelConfigTested"
    />
    <Transition name="toast"
      ><div v-if="toast" class="toast-message" :class="{ error: toast.error }" role="status">
        <Icon :name="toast.error ? 'Info' : 'CheckCircle2'" :size="19" />{{ toast.message
        }}<button class="icon-button" aria-label="关闭提示" @click="toast = null">
          <Icon name="X" :size="16" />
        </button></div
    ></Transition>
    <div
      v-if="activeReport || helpOpen || pendingDelete || pendingReportDelete"
      class="modal-backdrop"
      @click.self="closeDialogs"
      @keydown.esc="closeDialogs"
    >
      <section v-if="activeReport" class="modal" role="dialog" aria-modal="true" aria-labelledby="task-title">
        <div class="modal-header">
          <span class="eyebrow">REPORT TASK</span
          ><button class="icon-button" autofocus aria-label="关闭详情" @click="activeReport = null">
            <Icon name="X" />
          </button>
        </div>
        <h2 id="task-title">任务详情</h2>
        <p class="modal-subtitle">{{ activeReport.title }}</p>
        <div class="notice">
          <Icon name="Clock3" :size="18" /><span>{{
            activeReport.status === 'AWAITING_AGENT' ? '任务已保存，可以开始生成。' : activeReport.message
          }}</span>
        </div>
        <div class="task-meta">
          <span
            >分析模型<strong>{{ modelName(activeReport.modelId) }}</strong></span
          ><span
            >报告资料<strong>{{ activeReport.materialIds.length }} 份</strong></span
          ><span
            >创建时间<strong>{{ dateLabel(activeReport.createdAt) }}</strong></span
          >
        </div>
        <h4>输出内容</h4>
        <div class="task-output-list">
          <div v-for="output in activeOutputs" :key="output.id">
            <Icon :name="output.icon" :size="20" />
            <div>
              <strong>{{ output.name }}</strong>
              <p>{{ output.description }}</p>
            </div>
            <button
              v-if="activeReport.status === 'SUCCEEDED'"
              class="text-button"
              :disabled="!!downloading"
              @click="downloadOutput(output.id, output.name)"
            >
              {{ downloading === output.id ? '下载中…' : '下载 Word' }}
            </button>
          </div>
        </div>
        <h4>工作流状态</h4>
        <div class="task-timeline">
          <div
            v-for="(step, index) in activeReport.steps"
            :key="step.name"
            :class="{ complete: step.status === 'COMPLETED' }"
          >
            <span
              ><Icon v-if="step.status === 'COMPLETED'" name="Check" :size="15" /><template v-else>{{
                index + 1
              }}</template></span
            ><strong>{{ step.name }}</strong
            ><small :class="{ 'accent-text': step.status === 'RUNNING' }">{{ stepLabel(step.status) }}</small>
          </div>
        </div>
        <h4>报告范围</h4>
        <div class="task-chapters">
          <span v-for="c in activeReport.chapters" :key="c">{{ c }}</span>
          <span v-if="!isLegacyReport(activeReport)">第九章 个性化报告</span>
          <span v-if="activeReport.customTopic">第十章 {{ activeReport.customTopic.title }}</span>
        </div>
        <template v-if="activeReport.customTopic"><h4>主题分析要求</h4>
          <p class="task-instructions">{{ activeReport.customTopic.requirements }}</p>
        </template>
        <template v-if="activeReport.instructions"
          ><h4>补充要求</h4>
          <p class="task-instructions">{{ activeReport.instructions }}</p></template
        >
        <div class="modal-footer">
          <button class="button" @click="showReportHistory">查看报告记录</button
          ><button v-if="isActiveTask(activeReport)" class="button danger"
            :disabled="activeReport.status === 'CANCELLING' || !!stoppingId" @click="cancelReport(activeReport)">
            {{ activeReport.status === 'CANCELLING' || stoppingId === activeReport.id ? '正在终止…' : '终止生成' }}
          </button>
          <button v-else class="button danger" :disabled="running" @click="confirmReportDelete(activeReport)">删除记录</button>
          <button
            v-if="['DRAFT', 'AWAITING_AGENT', 'FAILED', 'INTERRUPTED', 'CANCELLED'].includes(activeReport.status)"
            class="button primary"
            :disabled="running"
            @click="runReport"
          >
            {{
              running
                ? '正在提交…'
                : ['FAILED', 'INTERRUPTED', 'CANCELLED'].includes(activeReport.status)
                  ? '继续生成'
                  : '开始生成'
            }}
          </button>
          <button v-else class="button" @click="activeReport = null">
            {{ activeReport.status === 'SUCCEEDED' ? '完成' : '关闭' }}
          </button>
        </div>
      </section>
      <section
        v-else-if="helpOpen"
        class="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="help-title"
      >
        <div class="modal-header">
          <span class="eyebrow">GETTING STARTED</span
          ><button class="icon-button" autofocus aria-label="关闭帮助" @click="helpOpen = false">
            <Icon name="X" />
          </button>
        </div>
        <h2 id="help-title">使用流程</h2>
        <div class="help-step">
          <span>01</span>
          <div>
            <h4>上传并选择资料</h4>
            <p>将调研数据、学校讲话、参考报告分类上传。可在资料库选择本次使用的文件。</p>
          </div>
        </div>
        <div class="help-step">
          <span>02</span>
          <div>
            <h4>配置并保存报告任务</h4>
            <p>填写学校与届别，勾选报告章节（默认全选），查看三个诊断维度，选择模型并补充分析要求。</p>
          </div>
        </div>
        <div class="help-step">
          <span>03</span>
          <div>
            <h4>生成并下载报告</h4>
            <p>在任务详情中开始生成并查看进度；完成后下载包含新增章节的完整报告。</p>
          </div>
        </div>
        <div class="modal-footer">
          <button class="button primary" @click="helpOpen = false">
            开始准备<Icon name="ArrowRight" :size="16" />
          </button>
        </div>
      </section>
      <section
        v-else-if="pendingReportDelete"
        class="modal compact" role="dialog" aria-modal="true" aria-labelledby="delete-report-title"
      >
        <div class="modal-header">
          <span class="eyebrow">REPORT HISTORY</span>
          <button class="icon-button" autofocus aria-label="取消删除" @click="pendingReportDelete = null"><Icon name="X" /></button>
        </div>
        <h2 id="delete-report-title">删除这条报告记录？</h2>
        <p class="modal-subtitle">{{ pendingReportDelete.title }}</p>
        <p>删除后将无法在页面查看或下载该任务的报告。已上传资料会保留。</p>
        <div class="modal-footer">
          <button class="button" :disabled="deletingReport" @click="pendingReportDelete = null">取消</button>
          <button class="button danger" :disabled="deletingReport" @click="deleteReport">{{ deletingReport ? '正在删除…' : '删除记录' }}</button>
        </div>
      </section>
      <section
        v-else-if="pendingDelete"
        class="modal compact"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-title"
      >
        <div class="modal-header">
          <span class="eyebrow">MATERIAL LIBRARY</span
          ><button class="icon-button" autofocus aria-label="取消删除" @click="pendingDelete = null">
            <Icon name="X" />
          </button>
        </div>
        <h2 id="delete-title">删除这份资料？</h2>
        <p class="modal-subtitle">{{ pendingDelete.name }}</p>
        <p>文件将从本地资料库移除。已被报告任务引用的资料无法删除。</p>
        <div class="modal-footer">
          <button class="button" @click="pendingDelete = null">取消</button
          ><button class="button danger" @click="deleteMaterial">删除资料</button>
        </div>
      </section>
    </div>
  </div>
</template>
