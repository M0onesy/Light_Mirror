import { Check } from 'lucide-react';
import type { Requirement } from '@/types/diagnosis';
import { RISK_FACTOR } from '@/hooks/useDiagnosis';
import Tag from './ui/Tag';
import { RedlineBadge } from './AmendmentBadge';

/**
 * 需求项卡片 —— 展示救回预估 / 成本 / 造假风险 / 红线约束，可勾选。
 */

export interface RequirementCardProps {
  requirement: Requirement;
  selected: boolean;
  onToggle: (id: string) => void;
  /** 是否被根因树/下钻联动高亮。 */
  highlighted?: boolean;
}

/** 造假风险 → 色调（仅「高」用红；其余中性/琥珀，因风险≠已违规）。 */
function riskTone(risk: Requirement['fake_risk']): 'neutral' | 'improveAlt' | 'alert' | 'ok' {
  switch (risk) {
    case '高':
      return 'alert';
    case '中':
      return 'improveAlt';
    case '低':
      return 'neutral';
    default:
      return 'ok';
  }
}

/** 需求卡片组件。 */
export function RequirementCard({
  requirement,
  selected,
  onToggle,
  highlighted = false,
}: RequirementCardProps) {
  const r = requirement;
  const riskWeight = r.rescue_estimate * RISK_FACTOR[r.fake_risk];

  return (
    <button
      type="button"
      onClick={() => onToggle(r.id)}
      className={[
        'mj-panel flex w-full flex-col gap-2 p-3 text-left transition-colors',
        selected ? 'border-improve/60 bg-improve/5' : 'hover:border-border/80',
        highlighted ? 'ring-1 ring-improve/40' : '',
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-2 min-w-0">
          {/* 勾选框 */}
          <span
            className={[
              'mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-sm border',
              selected ? 'border-improve bg-improve' : 'border-border',
            ].join(' ')}
          >
            {selected && <Check size={11} className="text-white" strokeWidth={3} />}
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="mj-num text-2xs text-text-muted">{r.id}</span>
              {r.is_redline && <RedlineBadge label="红线需求" />}
            </div>
            <div className="text-sm font-medium text-text-primary leading-snug">
              {r.title}
            </div>
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="mj-num text-md font-semibold text-improve">
            {r.rescue_estimate}
          </div>
          <div className="text-2xs text-text-muted">救回条数</div>
        </div>
      </div>

      {/* 元信息行 */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs">
        <span className="text-text-muted">
          成本 <span className="text-text-secondary">{r.cost}</span>
        </span>
        <span className="text-text-muted">
          造假风险{' '}
          <Tag tone={riskTone(r.fake_risk)} numeric>
            {r.fake_risk}
          </Tag>
        </span>
        {riskWeight > 0 && (
          <span
            className="text-text-muted"
            title="救回条数 × 造假风险权重（无=0/低=0.1/中=0.3/高=0.6）"
          >
            风险加权 <span className="mj-num text-text-secondary">{riskWeight.toFixed(1)}</span>
          </span>
        )}
      </div>

      {/* 红线约束 */}
      {r.redline_constraint !== null && (
        <div className="flex items-start gap-1.5 border-t border-border/60 pt-2 text-2xs text-text-muted">
          <span className="shrink-0 text-abstain">约束</span>
          <span className="text-text-secondary leading-snug">{r.redline_constraint}</span>
        </div>
      )}
    </button>
  );
}

export default RequirementCard;
