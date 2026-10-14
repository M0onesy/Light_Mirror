import { useMemo } from 'react';
import { ArrowRight, Info } from 'lucide-react';
import { useDiagnosis } from '@/hooks/useDiagnosis';
import MetricCard from '@/components/MetricCard';
import AbstentionBar from '@/components/AbstentionBar';
import Tag from '@/components/ui/Tag';
import type { Amendability } from '@/types/diagnosis';

/**
 * 结论总览视图 —— 30 秒看懂首屏。
 *
 * 首屏结构（结论层）：
 * 1. 一句话结论：313 弃权 = 可改善 N 条 + 待用户声明 M 条 + 有意保守 P 条。
 * 2. 北极星 EVR 大卡 + 阶段目标。
 * 3. 四条驱动指标。
 * 4. 三条反指标（=0 → 绿标「合规」）。
 * 5. 一键跳转构成分解。
 */

export interface OverviewViewProps {
  onNavigate: (view: 'composition' | 'rootcause' | 'drilldown' | 'simulator') => void;
}

/** 构成段 id → amendability（AbstentionBar 段点击回传 id 后需解析）。 */
export function OverviewView({ onNavigate }: OverviewViewProps) {
  const {
    data,
    activeVersion,
    denominator,
    evr,
    amendableCount,
    redlineCount,
    declareCount,
    compositionByAmendability,
  } = useDiagnosis();

  const { metrics } = data;

  /** AbstentionBar 段（按 amendability 聚合，id 即 amendability）。 */
  const segments = useMemo(
    () =>
      compositionByAmendability.map((s) => ({
        id: s.amendability,
        label: s.label,
        count: s.count,
        amendability: s.amendability as Amendability,
      })),
    [compositionByAmendability]
  );

  /** 可改善相关 X 码（用于结论句括注）。 */
  const amendableXCodes = useMemo(() => {
    const set = new Set<string>();
    for (const c of data.composition) {
      if (c.amendability === 'amendable' || c.amendability === 'amendable_partial') {
        set.add(c.x_code);
      }
    }
    return Array.from(set).sort().join('/');
  }, [data.composition]);

  /** 有意保守相关 X 码。 */
  const redlineXCodes = useMemo(() => {
    const set = new Set<string>();
    for (const c of data.composition) {
      if (c.amendability === 'intentional') {
        set.add(c.x_code);
      }
    }
    return Array.from(set).sort().join('/');
  }, [data.composition]);

  const hasCounterAlert = metrics.counter_metrics.some((c) => c.value > 0);

  return (
    <div className="flex flex-col gap-4">
      {/* —— 一句话结论 —— */}
      <section className="mj-panel p-4">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="mj-label">30 秒结论</span>
          <span className="text-2xs text-text-muted">
            版本 {activeVersion.label} · 分母 {denominator}（{data.meta.denominator_scope}）
          </span>
        </div>
        <p className="mt-2 text-lg leading-relaxed text-text-primary">
          <span className="mj-num font-semibold text-xl">
            {activeVersion.abstained}
          </span>{' '}
          条弃权 =
          <span className="mx-1 font-semibold text-improve">
            可改善 <span className="mj-num">{amendableCount}</span> 条
          </span>
          {amendableXCodes !== '' && (
            <span className="text-2xs text-text-muted">（{amendableXCodes}）</span>
          )}
          ＋
          <span className="mx-1 font-semibold text-declare">
            待用户声明 <span className="mj-num">{declareCount}</span> 条
          </span>
          ＋
          <span className="mx-1 font-semibold text-abstain">
            有意保守 <span className="mj-num">{redlineCount}</span> 条
          </span>
          {redlineXCodes !== '' && (
            <span className="text-2xs text-text-muted">（{redlineXCodes}·不可降）</span>
          )}
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => onNavigate('composition')}
            className="inline-flex items-center gap-1.5 rounded-sm border border-improve/40 bg-improve/10 px-3 py-1.5 text-sm font-medium text-improve hover:bg-improve/20"
          >
            查看构成分解与 X 码迁移
            <ArrowRight size={14} />
          </button>
          <button
            type="button"
            onClick={() => onNavigate('simulator')}
            className="inline-flex items-center gap-1.5 rounded-sm border border-border px-3 py-1.5 text-sm text-text-secondary hover:text-text-primary"
          >
            打开需求模拟器
            <ArrowRight size={14} />
          </button>
        </div>
      </section>

      {/* —— 北极星 EVR 大卡 —— */}
      <section className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <MetricCard
          variant="northstar"
          title={metrics.north_star.name}
          value={evr}
          unit="ratio"
          formula={`${metrics.north_star.formula} = ${activeVersion.verified + activeVersion.violated}/${denominator}`}
          guardrail={metrics.north_star.guardrail}
          stages={metrics.north_star.stages}
        />
        <div className="lg:col-span-2">
          <div className="mj-panel flex h-full flex-col justify-center gap-3 p-4">
            <span className="mj-label">弃权构成（按可改善性）</span>
            <AbstentionBar segments={segments} total={activeVersion.abstained} />
            <div className="flex items-center gap-1.5 text-2xs text-text-muted">
              <Info size={11} />
              <span>
                中性冷灰 = 合法弃权（理性的、被尊重的）；高亮 = 可改善空间；本题不涉及错误红。
              </span>
            </div>
          </div>
        </div>
      </section>

      {/* —— 驱动指标 —— */}
      <section className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="mj-label">驱动指标（近端杠杆）</span>
          <span className="text-2xs text-text-muted">
            现状多为估计，标注「待逐条核验」
          </span>
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {metrics.drivers.map((d) => (
            <MetricCard
              key={d.name}
              variant="driver"
              title={d.name}
              value={d.current_value}
              target={d.target}
              unit="ratio"
              leverage={d.leverage}
            />
          ))}
        </div>
      </section>

      {/* —— 结果指标 —— */}
      <section className="flex flex-col gap-2">
        <span className="mj-label">结果指标</span>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {metrics.outcomes.map((o) => (
            <MetricCard
              key={o.name}
              variant="driver"
              title={o.name}
              value={o.current_value}
              target={o.target}
              unit="ratio"
              status={o.status}
            />
          ))}
        </div>
      </section>

      {/* —— 反指标 —— */}
      <section className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <span className="mj-label">反指标（必须 = 0）</span>
          {hasCounterAlert ? (
            <Tag tone="alert">存在违规</Tag>
          ) : (
            <Tag tone="ok">全部合规</Tag>
          )}
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {metrics.counter_metrics.map((c) => (
            <MetricCard
              key={c.name}
              variant="counter"
              title={c.name}
              value={c.value}
              unit="count"
              isAlert={c.value > c.threshold}
            />
          ))}
        </div>
      </section>
    </div>
  );
}

export default OverviewView;
