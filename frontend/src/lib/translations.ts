export type Locale = 'zh-CN' | 'en-US'

const messages = {
  'zh-CN': {
    appTitle: 'NormaGraph',
    documents: 'Documents',
    knowledgeGraph: 'Knowledge Graph',
    retrieval: 'Retrieval',
    api: 'API',
    theme: 'Theme',
    language: 'Language',
    token: 'Token',
    uploadAndScan: '上传并扫描',
    retry: '重试',
    scan: '扫描',
    delete: '删除',
    pipeline: '流水线状态',
    searchNode: '搜索节点',
    layout: '布局',
    radial: '径向',
    hierarchy: '层级',
    retrievalPending: 'Retrieval 后端暂未实现，当前页面提供工作台布局和参数面板占位。',
    swagger: 'Swagger UI',
    openapi: 'OpenAPI JSON',
  },
  'en-US': {
    appTitle: 'NormaGraph',
    documents: 'Documents',
    knowledgeGraph: 'Knowledge Graph',
    retrieval: 'Retrieval',
    api: 'API',
    theme: 'Theme',
    language: 'Language',
    token: 'Token',
    uploadAndScan: 'Upload and scan',
    retry: 'Retry',
    scan: 'Scan',
    delete: 'Delete',
    pipeline: 'Pipeline',
    searchNode: 'Search node',
    layout: 'Layout',
    radial: 'Radial',
    hierarchy: 'Hierarchy',
    retrievalPending: 'Retrieval backend is not implemented yet. This page keeps the chat workspace and tuning panel ready.',
    swagger: 'Swagger UI',
    openapi: 'OpenAPI JSON',
  },
} as const

export function t(locale: Locale, key: keyof (typeof messages)['zh-CN']) {
  return messages[locale][key] ?? messages['zh-CN'][key]
}
