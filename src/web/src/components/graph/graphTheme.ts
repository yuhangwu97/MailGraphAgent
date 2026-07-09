/**
 * Shared theme for the "deep-space neon" knowledge graph.
 * Kept in one place so the canvas engine, legend and filters agree on
 * colors / labels / icons for every entity type.
 */

/** Neon node colors tuned for a dark background (business + RAGFlow native types). */
export const NODE_COLORS: Record<string, string> = {
  // Business types
  Company: '#2DE1C2',    // emerald neon
  Contact: '#FBBF24',    // amber gold
  Employee: '#818CF8',   // indigo
  Project: '#FB6F92',    // rose
  Email: '#38BDF8',      // sky cyan
  Department: '#A78BFA', // violet
  Attachment: '#F59E0B', // orange
  // RAGFlow GraphRAG native types
  Organization: '#2DE1C2',
  Person: '#FBBF24',
  Location: '#38BDF8',
  Event: '#F59E0B',
  Entity: '#94A3B8',
}

export const DEFAULT_NODE_COLOR = '#94A3B8'

export const LABEL_NAMES: Record<string, string> = {
  Company: '客户公司', Contact: '外部联系人', Employee: '内部人员',
  Project: '项目', Email: '邮件', Department: '部门', Entity: '其他',
  Organization: '组织', Person: '人物', Location: '地点', Event: '事件',
  Attachment: '附件',
}

export const LABEL_ICONS: Record<string, string> = {
  Company: '🏢', Contact: '👤', Employee: '👔',
  Project: '📋', Email: '✉️', Department: '🏛️', Entity: '📦',
  Organization: '🏢', Person: '👤', Location: '📍', Event: '📅',
  Attachment: '📎',
}

export const colorOf = (type: string) => NODE_COLORS[type] || DEFAULT_NODE_COLOR
export const nameOf = (type: string) => LABEL_NAMES[type] || type
export const iconOf = (type: string) => LABEL_ICONS[type] || '📦'
