import type {
  Catalog,
  Category,
  Material,
  ModelConfig,
  ModelConfigInput,
  ModelChannel,
  ReportInput,
  ReportTask,
} from './types'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, options)
  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: '服务暂时不可用，请稍后重试' }))
    throw new Error(error.message || `请求失败（${response.status}）`)
  }
  return response.status === 204 ? (undefined as T) : response.json()
}

export const api = {
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
    const response = await fetch(`/api/reports/${id}/outputs/${kind}`)
    if (!response.ok) {
      const error = await response.json().catch(() => ({ message: '下载失败，请重试' }))
      throw new Error(error.message || '下载失败，请重试')
    }
    const url = URL.createObjectURL(await response.blob())
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
