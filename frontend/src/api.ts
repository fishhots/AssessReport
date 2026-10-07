import type {
  Catalog,
  Category,
  Material,
  ModelConfig,
  ModelConfigInput,
  ModelChannel,
  ReportInput,
  ReportTask,
  AuthUser,
} from './types'

let sessionVersion = 0
let sessionUserId = ''
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message) }
}
export function setSessionUser(id: string) {
  sessionUserId = id
  sessionVersion += 1
}
function checkSession(response: Response, path: string, version: number) {
  if (version !== sessionVersion) throw new ApiError('登录状态已变更', 401)
  const responseUser = response.headers.get('X-Workspace-User')
  if (!path.startsWith('/auth/') && (response.status === 401 || (responseUser && responseUser !== sessionUserId))) {
    window.dispatchEvent(new Event('assess-session-expired'))
    throw new ApiError('登录已失效或账号已变更，请重新登录', 401)
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const version = sessionVersion
  const headers = new Headers(options?.headers)
  if (sessionUserId && !['/auth/login', '/auth/register'].includes(path)) headers.set('X-Expected-User', sessionUserId)
  const response = await fetch(`/api${path}`, { ...options, headers, credentials: 'same-origin' })
  checkSession(response, path, version)
  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: '服务暂时不可用，请稍后重试' }))
    throw new ApiError(error.message || `请求失败（${response.status}）`, response.status)
  }
  const result = response.status === 204 ? (undefined as T) : await response.json()
  checkSession(response, path, version)
  return result
}

export const api = {
  me: () => request<AuthUser>('/auth/me'),
  login: (account: string, password: string, rememberMe: boolean) => request<AuthUser>('/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ account, password, rememberMe }),
  }),
  register: (username: string, account: string, password: string, inviteCode: string) => request<AuthUser>('/auth/register', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, account, password, inviteCode }),
  }),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  catalog: () => request<Catalog>('/catalog'),
  materials: () => request<Material[]>('/materials'),
  reports: () => request<ReportTask[]>('/reports'),
  runReport: (id: string) => request<ReportTask>(`/reports/${id}/run`, { method: 'POST' }),
  cancelReport: (id: string) => request<ReportTask>(`/reports/${id}/cancel`, { method: 'POST' }),
  deleteReport: (id: string) => request<void>(`/reports/${id}`, { method: 'DELETE' }),
  testModel: (id: string) => request<{ status: string }>(`/models/${id}/test`, { method: 'POST' }),
  modelConfig: (id: string, channel?: ModelChannel) =>
    request<ModelConfig>(`/models/${id}/config${channel ? `?channel=${encodeURIComponent(channel)}` : ''}`),
  saveModelConfig: (id: string, config: ModelConfigInput) =>
    request<ModelConfig>(`/models/${id}/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    }),
  downloadOutput: async (id: string, kind: string, filename: string) => {
    const version = sessionVersion
    const path = `/reports/${id}/outputs/${kind}`
    const response = await fetch(`/api${path}`, { credentials: 'same-origin', headers: { 'X-Expected-User': sessionUserId } })
    checkSession(response, path, version)
    if (!response.ok) {
      const error = await response.json().catch(() => ({ message: '下载失败，请重试' }))
      throw new Error(error.message || '下载失败，请重试')
    }
    const blob = await response.blob()
    checkSession(response, path, version)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  },
  upload: (files: File[], category: Category) => {
    const body = new FormData()
    files.forEach((file) => body.append('files', file))
    body.append('category', category)
    return request<Material[]>('/materials', { method: 'POST', body })
  },
  deleteMaterial: (id: string) => request<void>(`/materials/${id}`, { method: 'DELETE' }),
  createReport: (input: ReportInput) =>
    request<ReportTask>('/reports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    }),
}
