import { AlertTriangle, ShieldCheck, Gauge } from 'lucide-react';
import type { MetricStage } from '@/types/diagnosis';

/**
 * 指标卡 —— 三态：北极星 / 驱动 / 反指标。
 * 支持阶段目标刻度（stages）与 null → 「待确认」显示。
 */

export interface MetricCardProps {
  variant: 'northstar' | 'driver' | 'counter';
  title: string;
  value: number | null;
  target?: number | null;
  unit?: 'ratio' | 'count';
  formula?: string;
  guardrail?: string;
  stages?: MetricStage[];
  leverage?: string;
  /** counter 专用：是否高亮告警（>0 时 true）。 */
  isAlert?: boolean;
  /** 可选的副标题/状态文案。 */
  status?: string;
}

/** 将 0-1 比例格式化百分比。 */
function formatValue(value: number | null, unit: 'ratio' | 'count'): string {
  if (value === null) {
    return '待确认';
  }
  if (unit === 'ratio') {
    return `${(value * 100).toFixed(1)}%`;
  }
  return `${value}`;
}

/** 指标卡组件。 */
export function MetricCard({
  variant,
  title,
  value,
  target = null,
  unit = 'ratio',
  formula,
  guardrail,
  stages,
  leverage,
  isAlert = false,
  status,
}: MetricCardProps) {
  const isNorthStar = variant === 'northstar';
  const isCounter = variant === 'counter';

  return (
    <div
      className={[
        'mj-panel flex flex-col gap-2 p-3',
        isNorthStar ? 'border-improve/30' : '',
        isCounter && isAlert ? 'border-alert/60' : '',
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="mj-label truncate" title={title}>
          {title}
        </span>
        {isNorthStar && <Gauge size={14} className="text-improve" />}
        {isCounter &&
          (isAlert ? (
            <AlertTriangle size={14} className="text-alert" />
          ) : (
            <ShieldCheck size={14} className="text-ok" />
          ))}
      </div>

      <div className="flex items-baseline gap-1.5">
        <span
          className={[
            'mj-num font-semibold leading-none',
            isNorthStar ? 'text-2xl text-text-primary' : 'text-lg',
            !isNorthStar && !isCounter ? 'text-text-primary' : '',
            isCounter && isAlert ? 'text-alert' : '',
            isCounter && !isAlert ? 'text-ok' : '',
            value === null ? 'text-text-muted text-md' : '',
          ]
            .filter(Boolean)
            .join(' ')}
        >
          {formatValue(value, unit)}
        </span>
        {target !== null && variant === 'driver' && (
          <span className="mj-num text-2xs text-text-muted">
            → {formatValue(target, unit)}
          </span>
        )}
        {isCounter && (
          <span className="text-2xs text-text-muted">
            {isAlert ? '违规' : '合规'}
          </span>
        )}
      </div>

      {/* 驱动指标：当前→目标 进度条 */}
      {variant === 'driver' && target !== null && value !== null && (
        <div className="h-1 w-full bg-abstain-dim/40 rounded-sm overflow-hidden">
          <div
            className="h-full bg-improve"
            style={{ width: `${Math.min(value / target, 1) * 100}%` }}
          />
        </div>
      )}

      {/* 北极星：阶段目标刻度 */}
      {isNorthStar && stages !== undefined && stages.length > 0 && (
        <div className="mt-0.5 flex items-center gap-2">
          {stages.map((s) => (
            <div key={s.label} className="flex flex-col gap-0.5">
              <span className="text-2xs text-text-muted">{s.label}</span>
              <span className="mj-num text-xs text-text-secondary">
                {(s.value * 100).toFixed(1)}%
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 脚注：公式 / 护栏 / 杠杆 / 状态 */}
      <div className="mt-auto flex flex-wrap items-center gap-x-2 gap-y-0.5 text-2xs text-text-muted">
        {status !== undefined && <span className="text-improve-alt">{status}</span>}
        {formula !== undefined && <span className="truncate" title={formula}>{formula}</span>}
        {leverage !== undefined && <span>杠杆 {leverage}</span>}
        {guardrail !== undefined && <span className="text-text-muted">护栏 {guardrail}</span>}
      </div>
    </div>
  );
}

export default MetricCard;
