export type Category = 'DATA' | 'SPEECH' | 'REFERENCE'
export interface AuthUser {
  id: string
  username: string
  account: string
}
export interface Material {
  id: string
  name: string
  size: number
  category: Category
  createdAt: string
}
export interface ModelOption {
  id: string
  name: string
  provider: string
  description: string
  accent: string
  configured: boolean
}
export type ModelChannel = 'official' | 'zhizengzeng'
export interface ModelConfig {
  id: string
  model: string
  baseUrl: string
  hasApiKey: boolean
  keyMasked: string
  extraBody: Record<string, unknown>
  configured: boolean
  revision: number
  updatedAt: string | null
  reasoningMode: 'auto' | 'on' | 'off'
  reasoningEffort: 'low' | 'medium' | 'high'
  thinkingBudget: number
  channel: ModelChannel
  activeChannel: ModelChannel
}
export interface ModelConfigInput {
  channel: ModelChannel
  reasoningMode: 'auto' | 'on' | 'off'
  reasoningEffort: 'low' | 'medium' | 'high'
  thinkingBudget: number
  model: string
  baseUrl: string
  apiKey?: string
  extraBody: Record<string, unknown>
  revision: number
}
export interface ReportTemplate {
  id: string
  name: string
  description: string
  tag: string
  chapters: string[]
}
export interface Catalog {
  models: ModelOption[]
  templates: ReportTemplate[]
  agentConnected: boolean
}
export interface ReportTask {
  id: string
  title: string
  school: string
  year: number
  modelId: string
  templateId: string
  materialIds: string[]
  chapters: string[]
  instructions: string
  customTopic?: { title: string; requirements: string } | null
  status: string
  createdAt: string
  steps: { name: string; status: string }[]
  message: string
}
export type ReportInput = Pick<
  ReportTask,
  'school' | 'year' | 'modelId' | 'templateId' | 'materialIds' | 'chapters' | 'instructions' | 'customTopic'
>
