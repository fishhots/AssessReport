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
  webSearch?: WebSearchDirective | null
}
export type ReportInput = Pick<
  ReportTask,
  'school' | 'year' | 'modelId' | 'templateId' | 'materialIds' | 'chapters' | 'instructions' | 'customTopic' | 'webSearch'
>

export interface WebSearchTopic {
  id: string
  label: string
  category: Category
  terms: string
}
export interface WebSearchStatus {
  provider: string
  configured: boolean
  keySource: string
  baseUrl: string
  searchEndpoint: string
  fetchEndpoint: string
  enabled: boolean
  cacheDays: number
  maxPages: number
  topics: WebSearchTopic[]
  archive: { total: number; pending: number; schools: string[]; lastFetchedAt: string | null }
  hint: string
}
export interface WebSearchDirective {
  enabled?: boolean
  school: string
  site?: string
  topics: string[]
  keywords?: string
  category?: Category | null
  maxResults?: number
  maxPages?: number
  afterYear?: number | null
  refresh?: boolean
  fetchContent?: boolean
}
export interface WebSearchQuery {
  topic: string
  label: string
  category: Category
  query: string
  queryKey: string
}
export interface WebSearchError {
  topic?: string
  query?: string
  code: string
  message: string
}
export interface WebArchiveEntry {
  id: string
  url: string
  title: string
  siteName: string
  host: string
  school: string
  site: string
  topic: string
  topicLabel: string
  query: string
  queryKey: string
  category: Category
  snippet: string
  contentChars: number
  source: string
  authority: number | null
  rerankScore: number | null
  publishedAt: string | null
  fetchedAt: string
  materialId: string
  reused?: boolean
  content?: string
}
export interface WebArchiveStats {
  total: number
  pending: number
  schools: string[]
  lastFetchedAt: string | null
}
export interface WebSearchPreview {
  school: string
  site: string
  maxResults: number
  maxPages: number
  afterYear: number | null
  refresh: boolean
  queries: WebSearchQuery[]
}
export interface WebSearchRunResult {
  school?: string
  site?: string
  queries?: WebSearchQuery[]
  entries: WebArchiveEntry[]
  reused?: number
  count?: number
  errors?: WebSearchError[]
  materials: Material[]
  imported: number
}
export interface WebArchiveList {
  entries: WebArchiveEntry[]
  stats: WebArchiveStats
}
