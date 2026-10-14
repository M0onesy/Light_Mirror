import { ShieldCheck, TrendingUp, CircleDashed, AlertTriangle, PenLine } from 'lucide-react';
import type { Amendability } from '@/types/diagnosis';

/**
 * 可改善/红线/中性 语义徽章 —— 核心叙事红线的视觉载体。
 *
 * 颜色语义（架构 §8.1）：
 * - amendable / amendable_partial → 高亮（青/琥珀）= 机会
 * - needs_user_declaration → 紫 = 需人工介入
 * - intentional → 中性冷灰 = 理性的、被尊重的合法弃权，绝不用红
 */

export interface AmendmentBadgeProps {
  amendability: Amendability;
  compact?: boolean;
}

interface BadgeSpec {
  label: string;
  cls: string;
  Icon: typeof ShieldCheck;
}

const SPEC: Record<Amendability, BadgeSpec> = {
  amendable: {
    label: '可改善',
    cls: 'text-improve bg-improve/10 border-improve/30',
    Icon: TrendingUp,
  },
  amendable_partial: {
    label: '部分可改善',
    cls: 'text-improve-alt bg-improve-alt/10 border-improve-alt/30',
    Icon: CircleDashed,
  },
  needs_user_declaration: {
    label: '待用户声明',
    cls: 'text-declare bg-declare/10 border-declare/30',
    Icon: PenLine,
  },
  intentional: {
    label: '有意保守',
    cls: 'text-abstain bg-abstain/10 border-abstain/30',
    Icon: ShieldCheck,
  },
};

/** 徽章组件。 */
export function AmendmentBadge({ amendability, compact = false }: AmendmentBadgeProps) {
  const spec = SPEC[amendability];
  const { Icon } = spec;
  return (
    <span
      className={[
        'inline-flex items-center gap-1 rounded-sm border text-2xs font-medium leading-none',
        compact ? 'px-1 py-0.5' : 'px-1.5 py-0.5',
        spec.cls,
      ].join(' ')}
      title={
        amendability === 'intentional'
          ? '设计上有意保守：护城河，不可降'
          : undefined
      }
    >
      <Icon size={compact ? 10 : 11} strokeWidth={2} />
      {spec.label}
    </span>
  );
}

/** 红线警示徽章（仅真正违规时使用红色）。 */
export function RedlineBadge({ label = '红线' }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-sm border border-alert/50 bg-alert/10 px-1.5 py-0.5 text-2xs font-medium leading-none text-alert">
      <AlertTriangle size={11} strokeWidth={2} />
      {label}
    </span>
  );
}

export default AmendmentBadge;
