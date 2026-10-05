<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'
import type { ModelChannel, ModelConfig, ModelOption } from '../types'
import Icon from './Icon.vue'

const props = defineProps<{ provider: ModelOption }>()
const emit = defineEmits<{ close: []; saved: [config: ModelConfig]; tested: [id: string, passed: boolean] }>()
const configuration = ref<ModelConfig | null>(null)
const channel = ref<ModelChannel>('official')
const supportsRelay = computed(() => ['gpt', 'claude', 'gemini'].includes(props.provider.id))
const model = ref('')
const baseUrl = ref('')
const apiKey = ref('')
const extraBody = ref('{}')
const reasoningMode = ref<'auto' | 'on' | 'off'>('auto')
const reasoningEffort = ref<'low' | 'medium' | 'high'>('medium')
const thinkingBudget = ref(4096)
const showKey = ref(false)
const loading = ref(true)
const busy = ref('')
const message = ref('')
const error = ref('')
const form = ref<HTMLFormElement>()
const dialog = ref<HTMLElement>()
const nameInput = ref<HTMLInputElement>()
const closeButton = ref<HTMLButtonElement>()
const canSave = computed(
  () =>
    !loading.value &&
    !busy.value &&
    !!configuration.value &&
    !!model.value.trim() &&
    !!baseUrl.value.trim() &&
    (!!apiKey.value.trim() || configuration.value.hasApiKey),
)
const previousFocus = document.activeElement as HTMLElement | null
const previousOverflow = document.body.style.overflow
type ProfileDraft = {
  configuration: ModelConfig; model: string; baseUrl: string; apiKey: string; extraBody: string
  reasoningMode: 'auto' | 'on' | 'off'; reasoningEffort: 'low' | 'medium' | 'high'; thinkingBudget: number
}
const drafts = new Map<ModelChannel, ProfileDraft>()
let disposed = false
watch(
  [model, baseUrl, apiKey, extraBody, reasoningMode, reasoningEffort, thinkingBudget],
  () => {
    if (!busy.value) {
      message.value = ''
      error.value = ''
    }
  },
  { flush: 'sync' },
)

function close() {
  apiKey.value = ''
  drafts.clear()
  emit('close')
}
function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.stopPropagation()
    close()
  }
  if (event.key !== 'Tab') return
  const elements = Array.from(
    dialog.value?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, a[href]',
    ) || [],
  ).filter((element) => element.getClientRects().length > 0)
  const first = elements[0],
    last = elements[elements.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}
function captureDraft(): ProfileDraft {
  return { configuration: configuration.value!, model: model.value, baseUrl: baseUrl.value, apiKey: apiKey.value,
    extraBody: extraBody.value, reasoningMode: reasoningMode.value, reasoningEffort: reasoningEffort.value,
    thinkingBudget: thinkingBudget.value }
}
function applyDraft(draft: ProfileDraft) {
  configuration.value = draft.configuration
  channel.value = draft.configuration.channel || 'official'
  model.value = draft.model
  baseUrl.value = draft.baseUrl
  apiKey.value = draft.apiKey
  extraBody.value = draft.extraBody
  reasoningMode.value = draft.reasoningMode
  reasoningEffort.value = draft.reasoningEffort
  thinkingBudget.value = draft.thinkingBudget
  showKey.value = false
}
function applyConfig(saved: ModelConfig) {
  applyDraft({ configuration: saved, model: saved.model, baseUrl: saved.baseUrl, apiKey: '',
    extraBody: JSON.stringify(saved.extraBody, null, 2), reasoningMode: saved.reasoningMode || 'auto',
    reasoningEffort: saved.reasoningEffort || 'medium', thinkingBudget: saved.thinkingBudget || 4096 })
}
async function switchChannel(event: Event) {
  const next = (event.target as HTMLSelectElement).value as ModelChannel
  if (!configuration.value || busy.value || loading.value || next === channel.value) return
  const previous = channel.value
  drafts.set(previous, captureDraft())
  message.value = ''
  error.value = ''
  apiKey.value = ''
  const cached = drafts.get(next)
  if (cached) { applyDraft(cached); return }
  loading.value = true
  try {
    const saved = await api.modelConfig(props.provider.id, next)
    if (!disposed) applyConfig(saved)
  } catch (failure) {
    if (!disposed) {
      applyDraft(drafts.get(previous)!)
      error.value = failure instanceof Error ? failure.message : '配置读取失败'
    }
  } finally {
    if (!disposed) loading.value = false
  }
}
async function load() {
  loading.value = true
  error.value = ''
  try {
    const saved = await api.modelConfig(props.provider.id)
    if (disposed) return
    applyConfig(saved)
  } catch (failure) {
    if (!disposed) error.value = failure instanceof Error ? failure.message : '配置读取失败'
  } finally {
    if (!disposed) loading.value = false
  }
  if (!disposed && configuration.value) {
    await nextTick()
    nameInput.value?.focus()
  }
}
async function save(test = false) {
  if (!canSave.value || !form.value?.reportValidity() || !configuration.value) return
  error.value = ''
  message.value = ''
  let extra: Record<string, unknown>
  try {
    const parsed = JSON.parse(extraBody.value.trim() || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error()
    extra = parsed
  } catch {
    error.value = '额外参数请填写 JSON 对象，例如 {}'
    return
  }
  busy.value = 'saving'
  let savedSuccessfully = false
  try {
    const saved = await api.saveModelConfig(props.provider.id, {
      model: model.value.trim(),
      baseUrl: baseUrl.value.trim(),
      ...(apiKey.value.trim() ? { apiKey: apiKey.value.trim() } : {}),
      extraBody: extra,
      revision: configuration.value.revision,
      channel: channel.value,
      reasoningMode: reasoningMode.value,
      reasoningEffort: reasoningEffort.value,
      thinkingBudget: thinkingBudget.value,
    })
    apiKey.value = ''
    showKey.value = false
    savedSuccessfully = true
    emit('saved', saved)
    if (disposed) return
    applyConfig(saved)
    // Only this dialog's successful save advances cached profiles' revisions.
    // Updates from another window still fail the server's concurrency check.
    for (const draft of drafts.values()) {
      draft.configuration = { ...draft.configuration, revision: saved.revision, activeChannel: saved.channel }
    }
    drafts.set(saved.channel, captureDraft())
    message.value = '配置已保存'
    if (test) {
      busy.value = 'testing'
      await api.testModel(props.provider.id)
      emit('tested', props.provider.id, true)
      if (!disposed) message.value = '配置已保存，连接测试通过'
    }
  } catch (failure) {
    if (savedSuccessfully && test) emit('tested', props.provider.id, false)
    if (!disposed) error.value = failure instanceof Error ? failure.message : '配置保存失败'
  } finally {
    if (!disposed) busy.value = ''
  }
}
onMounted(() => {
  document.body.style.overflow = 'hidden'
  closeButton.value?.focus()
  void load()
})
onUnmounted(() => {
  disposed = true
  apiKey.value = ''
  drafts.clear()
  document.body.style.overflow = previousOverflow
  previousFocus?.focus()
})
</script>

<template>
  <div class="modal-backdrop" @click.self="close" @keydown="keydown">
    <section
      ref="dialog"
      class="modal model-config-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="model-config-title"
    >
      <div class="modal-header">
        <span class="eyebrow">MODEL SETTINGS</span>
        <button ref="closeButton" class="icon-button" aria-label="关闭模型设置" @click="close">
          <Icon name="X" />
        </button>
      </div>
      <h2 id="model-config-title">{{ provider.name }} 模型设置</h2>
      <p class="modal-subtitle">{{ provider.provider }}</p>
      <div v-if="loading" class="model-config-loading" role="status">
        <Icon name="Loader2" class="spin" :size="18" />正在读取配置…
      </div>
      <form v-else-if="configuration" ref="form" class="model-config-form" @submit.prevent="save()">
        <label v-if="supportsRelay" class="field">
          <span>调用方式</span>
          <select name="channel" :value="channel" :disabled="!!busy" @change="switchChannel">
            <option value="zhizengzeng">智增增中转</option>
            <option value="official">官方直连</option>
          </select>
          <small>两种方式的配置分别保存。保存后，新任务将使用所选方式。</small>
        </label>
        <p v-if="supportsRelay && channel === 'zhizengzeng'" class="model-extra-help">
          使用智增增平台的 API Key 和其支持的模型名称。
          <a href="https://doc.zhizengzeng.com/doc-3979947" target="_blank" rel="noopener noreferrer">查看模型列表</a>
        </p>
        <label class="field">
          <span>模型名称 <b>*</b></span>
          <input
            ref="nameInput"
            v-model="model"
            name="model"
            required
            maxlength="512"
            placeholder="填写实际调用的模型名称"
            autocomplete="off"
            :disabled="!!busy"
          />
        </label>
        <label class="field">
          <span>接口地址 <b>*</b></span>
          <input
            v-model="baseUrl"
            name="baseUrl"
            type="url"
            required
            maxlength="2048"
            placeholder="填写接口基础地址（Base URL）"
            autocomplete="off"
            :disabled="!!busy"
          />
        </label>
        <div class="field">
          <label for="model-api-key"
            >API Key <b v-if="!configuration.hasApiKey">*</b
            ><span v-else class="model-key-saved">已保存</span></label
          >
          <div class="model-key-input">
            <input
              id="model-api-key"
              v-model="apiKey"
              name="apiKey"
              :type="showKey ? 'text' : 'password'"
              :required="!configuration.hasApiKey"
              maxlength="4096"
              :placeholder="configuration.hasApiKey ? '留空保持此调用方式的密钥，填写新密钥可替换' : channel === 'zhizengzeng' ? '填写智增增平台 API Key' : '填写模型官方 API Key'"
              autocomplete="new-password"
              :disabled="!!busy"
            />
            <button
              type="button"
              class="text-button"
              :disabled="!!busy"
              :aria-pressed="showKey"
              @click="showKey = !showKey"
            >
              {{ showKey ? '隐藏' : '显示' }}
            </button>
          </div>
          <small v-if="configuration.hasApiKey">已保存的密钥不会回显。</small>
        </div>
        <label class="field"><span>章节深度分析</span>
          <select v-model="reasoningMode" :disabled="!!busy">
            <option value="auto">自动适配模型（推荐）</option>
            <option value="on">启用思考模式</option>
            <option value="off">关闭额外深度分析</option>
          </select>
          <small>用于模板章节、个性化报告和主题分析；模型须支持思考模式。</small>
        </label>
        <div v-if="reasoningMode !== 'off'" class="model-reasoning-options">
          <label class="field"><span>思考强度</span><select v-model="reasoningEffort" :disabled="!!busy">
            <option value="low">低</option><option value="medium">中</option><option value="high">高</option>
          </select></label>
          <label v-if="['qwen', 'claude'].includes(provider.id)" class="field"><span>思考预算</span>
            <input v-model.number="thinkingBudget" type="number" min="1024" max="16000" step="1024" :disabled="!!busy" />
          </label>
        </div>
        <details class="model-extra-settings">
          <summary>额外参数（可选）</summary>
          <p v-if="provider.id === 'deepseek'" class="model-extra-help">系统按阶段适配思考开关，章节分析开启思考，结构化成文使用兼容参数。</p>
          <label class="field"
            ><span>JSON 参数</span>
            <textarea
              v-model="extraBody"
              name="extraBody"
              rows="4"
              maxlength="16384"
              spellcheck="false"
              :disabled="!!busy"
              placeholder="{}"
            />
          </label>
        </details>
        <p v-if="message" class="model-config-success" role="status">
          <Icon name="CheckCircle2" :size="17" />{{ message }}
        </p>
        <p v-if="error" class="model-config-error" role="alert">{{ error }}</p>
        <div class="modal-footer model-config-actions">
          <button type="button" class="button" @click="close">关闭</button>
          <button type="submit" class="button" :disabled="!canSave">
            {{ busy === 'saving' ? '正在保存…' : '保存配置' }}
          </button>
          <button type="button" class="button primary" :disabled="!canSave" @click="save(true)">
            {{ busy === 'testing' ? '正在测试…' : '保存并测试' }}<Icon name="ArrowRight" :size="16" />
          </button>
        </div>
      </form>
      <div v-else>
        <p class="model-config-error" role="alert">{{ error }}</p>
        <button class="button" @click="load">重新读取</button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.model-config-modal {
  max-width: 600px;
}
.model-config-form {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.model-config-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 30px 0;
  color: var(--text-secondary);
}
.model-config-form .field {
  margin: 0;
}
.model-config-form .field input {
  font-size: 13px;
}
.model-config-form .field > label {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  font-weight: 500;
  margin-bottom: 10px;
}
.model-key-saved {
  margin-left: auto;
  color: var(--success);
  font-size: 11px;
}
.model-key-input {
  display: flex;
  position: relative;
}
.model-key-input input {
  width: 100%;
  padding-right: 55px;
}
.model-key-input button {
  position: absolute;
  right: 14px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 12px;
}
.model-extra-settings {
  font-size: 12px;
  color: var(--text-secondary);
}
.model-extra-settings summary {
  cursor: pointer;
  padding: 3px 0;
}
.model-extra-settings .field {
  margin-top: 16px;
}
.model-extra-help {
  margin-top: 12px;
  line-height: 1.7;
}
.model-extra-help p {
  margin: 0 0 6px;
}
.model-extra-help button {
  font-size: 12px;
}
.model-reasoning-options {
  display: flex;
  gap: 16px;
}
.model-reasoning-options .field { flex: 1; }
.model-extra-settings textarea {
  font-family: ui-monospace, monospace;
  font-size: 12px;
  resize: vertical;
}
.model-config-success {
  color: var(--success);
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0;
}
.model-config-error {
  color: var(--danger);
  font-size: 12px;
  line-height: 1.7;
  margin: 0;
}
.model-config-actions {
  margin-top: 4px;
  flex-wrap: wrap;
}
.model-config-actions .button {
  min-width: 105px;
}
@media (max-width: 600px) {
  .model-config-actions .button {
    flex: 1;
  }
}
</style>
