import type { ReactNode } from 'react';

/**
 * 通用标签/数值片 —— 高密度信息展示原子组件。
 * 支持语义色调（中性/高亮/紫/红/绿），默认中性冷灰（弃权语义）。
 */

export type TagTone =
  | 'neutral'
  | 'abstain'
  | 'improve'
  | 'improveAlt'
  | 'declare'
  | 'alert'
  | 'ok';

export interface TagProps {
  children: ReactNode;
  tone?: TagTone;
  /** 是否使用等宽数字。 */
  numeric?: boolean;
  /** 是否加边框（默认无边框，仅底色）。 */
  outlined?: boolean;
  title?: string;
  className?: string;
}

const TONE_CLASS: Record<TagTone, string> = {
  neutral: 'text-text-secondary bg-white/5',
  abstain: 'text-abstain bg-abstain/10',
  improve: 'text-improve bg-improve/10',
  improveAlt: 'text-improve-alt bg-improve-alt/10',
  declare: 'text-declare bg-declare/10',
  alert: 'text-alert bg-alert/10',
  ok: 'text-ok bg-ok/10',
};

const OUTLINE_CLASS: Record<TagTone, string> = {
  neutral: 'border-border',
  abstain: 'border-abstain/40',
  improve: 'border-improve/40',
  improveAlt: 'border-improve-alt/40',
  declare: 'border-declare/40',
  alert: 'border-alert/50',
  ok: 'border-ok/40',
};

/** 标签组件。 */
export function Tag({
  children,
  tone = 'neutral',
  numeric = false,
  outlined = false,
  title,
  className = '',
}: TagProps) {
  return (
    <span
      title={title}
      className={[
        'inline-flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-2xs leading-none font-medium',
        TONE_CLASS[tone],
        outlined ? `border ${OUTLINE_CLASS[tone]}` : '',
        numeric ? 'mj-num' : '',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
    >
      {children}
    </span>
  );
}

export default Tag;
