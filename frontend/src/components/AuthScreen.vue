<script setup lang="ts">
import { ref } from 'vue'
import Icon from './Icon.vue'
import { api } from '../api'
import type { AuthUser } from '../types'

defineProps<{ sessionError?: string }>()
const emit = defineEmits<{ 'logged-in': [user: AuthUser] }>()
const registering = ref(false)
const username = ref('')
const account = ref('')
const password = ref('')
const inviteCode = ref('')
const rememberMe = ref(false)
const busy = ref(false)
const error = ref('')
const success = ref('')
function changeMode(register: boolean) {
  registering.value = register
  password.value = ''
  inviteCode.value = ''
  error.value = ''
  success.value = ''
}
async function submit() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  success.value = ''
  try {
    if (registering.value) {
      await api.register(username.value.trim(), account.value.trim(), password.value, inviteCode.value)
      registering.value = false
      password.value = ''
      inviteCode.value = ''
      success.value = '注册成功，请使用账号和密码登录。'
    } else {
      const user = await api.login(account.value.trim(), password.value, rememberMe.value)
      password.value = ''
      emit('logged-in', user)
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '操作失败，请重试'
  } finally { busy.value = false }
}
</script>

<template>
  <main class="auth-page">
    <div class="auth-brand"><span class="brand-mark"><Icon name="GraduationCap" :size="25" /></span><strong>教学质量报告智能体</strong></div>
    <div class="auth-layout">
      <section class="auth-intro">
        <span class="eyebrow">TEACHING QUALITY REPORT</span>
        <h1>让每份报告，<br />都有可靠依据。</h1>
        <p>汇集校本资料，分析培养成效，生成完整的教学质量报告。</p>
        <div class="auth-feature"><Icon name="FolderOpen" :size="20" /><span>个人资料库，集中管理报告依据</span></div>
        <div class="auth-feature"><Icon name="FileText" :size="20" /><span>报告记录与进度，随时查看</span></div>
        <div class="auth-feature"><Icon name="ShieldCheck" :size="20" /><span>模型配置与资料按账号独立保存</span></div>
      </section>
      <section class="auth-card" aria-labelledby="auth-title">
        <div class="auth-tabs"><button :class="{ active: !registering }" :disabled="busy" @click="changeMode(false)">登录</button><button :class="{ active: registering }" :disabled="busy" @click="changeMode(true)">注册</button></div>
        <h2 id="auth-title">{{ registering ? '创建账号' : '登录工作空间' }}</h2>
        <p class="auth-subtitle">{{ registering ? '填写账号信息和邀请码，创建你的工作空间。' : '输入账号和密码，继续你的报告工作。' }}</p>
        <form @submit.prevent="submit">
          <label v-if="registering" class="field"><span>用户名</span><input v-model="username" autocomplete="nickname" maxlength="50" required placeholder="用于页面显示的用户名" :disabled="busy" /></label>
          <label class="field"><span>账号</span><input v-model="account" autocomplete="username" minlength="3" maxlength="64" :pattern="registering ? '[A-Za-z0-9_.\\-]{3,64}' : undefined" required placeholder="请输入登录账号" :disabled="busy" /><small v-if="registering">3–64位字母、数字、点、下划线或短横线</small></label>
          <label class="field"><span>密码</span><input v-model="password" type="password" :autocomplete="registering ? 'new-password' : 'current-password'" :minlength="registering ? 8 : undefined" maxlength="128" required :placeholder="registering ? '请设置至少8位的密码' : '请输入密码'" :disabled="busy" /></label>
          <label v-if="registering" class="field"><span>邀请码</span><input v-model="inviteCode" autocomplete="off" maxlength="64" required placeholder="请输入邀请码" :disabled="busy" /></label>
          <label v-else class="auth-remember"><input v-model="rememberMe" type="checkbox" :disabled="busy" />自动登录（30天）</label>
          <p v-if="error || sessionError" class="auth-error" role="alert">{{ error || sessionError }}</p>
          <p v-if="success" class="auth-success" role="status">{{ success }}</p>
          <button class="button primary auth-submit" :disabled="busy"><Icon :name="busy ? 'LoaderCircle' : registering ? 'UserPlus' : 'LogIn'" :class="{ spin: busy }" :size="18" />{{ busy ? '正在处理…' : registering ? '注册账号' : '登录' }}</button>
        </form>
      </section>
    </div>
  </main>
</template>
