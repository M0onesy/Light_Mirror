/**
 * 设计 token —— JS 侧颜色常量（SVG 取色的**唯一来源**）。
 *
 * 值必须与 `tailwind.config.ts` 的 `colors` **完全一致**（见 DESIGN §2.1/§2.2）。
 * 组件内**禁止**书写十六进制色字面量；唯一豁免为本文件与 `src/index.css` 的 `:root`。
 *
 * 核心叙事约定（不可违反）：
 * - 合法弃权 = abstain（冷灰，**绝不用红**）；可改善 = improve / improveAlt；
 * - 待声明 = declare（紫）；仅反指标 >0 / 红线 = alert（红）；合规 = ok（绿）。
 */
export const tokens = {
  // 背景
  bg: '#F5F7FA',
  panel: '#FFFFFF',
  panelAlt: '#FAFBFD',
  // 正文 / 弱化文字
  ink: '#1F2A37',
  muted: '#5C6B7A',
  mutedDim: '#8593A3',
  // 边框 / 分隔线
  line: '#DDE3EA',
  lineSoft: '#EAEFF4',
  // 强调
  accent: '#3B5B8C',
  accentSoft: '#EEF2F8',
  // 合法弃权（冷灰）
  abstain: '#64748B',
  abstainMuted: '#7C8998',
  abstainDim: '#C9D2DC',
  // 可改善（深青 / 深琥珀）
  improve: '#0E7490',
  improveAlt: '#B45309',
  // 待声明（紫）
  declare: '#6D28D9',
  // 警戒（仅真正违规）
  alert: '#DC2626',
  alertSoft: '#FEE2E2',
  // 合规（绿）
  ok: '#15803D',
  // 提示（琥珀棕）
  warn: '#8A6D3B',
  warnSoft: '#F7F2E6',
  // 命令块底（唯一保留的深底）
  cmdBg: '#0F172A',
} as const;

/** 颜色常量键类型。 */
export type TokenKey = keyof typeof tokens;

/**
 * 填色块上的近白文字 —— 浅色块（青/琥珀/灰/紫）上固定使用，保证可读。
 * 供 SVG 段内标签取色（见 DESIGN §4.2A `AbstentionBar` 第 88 行）。
 */
export const ON_FILL_TEXT = '#FFFFFF';

export default tokens;
