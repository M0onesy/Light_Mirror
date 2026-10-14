import { useMemo, useState } from 'react';
import { AlertTriangle, RotateCcw, TrendingUp, ShieldX } from 'lucide-react';
import { useDiagnosis } from '@/hooks/useDiagnosis';
import RequirementCard from '@/components/RequirementCard';
import Tag from '@/components/ui/Tag';
import type { Priority } from '@/types/diagnosis';

/**
 * 需求模拟器视图 —— 需求池 P0/P1/P2 勾选 → 模拟救回。
 *
 * - 勾选需求 → 实时计算「救回条数 / EVR 现值→模拟值 / 风险加权 / 红线告警」。
 * - 展示「不做清单」红区 5 条红线。
 * - simulate() 按 linked_causes 去重防重复计数，fake_risk 加权。
 */

const PRIORITY_LABEL: Record<Priority, string> = {
  P0: 'P0 · Must have（直接决定产品是否有价值）',
  P1: 'P1 · Should have（显著提升覆盖）',
  P2: 'P2 · Nice to have（边界改善）',
};

/** 模拟器视图组件。 */
export function SimulatorView() {
  const {
    data,
    activeVersion,
    denominator,
    evr,
    requirementsByPriority,
    simulate,
  } = useDiagnosis();

  /** 勾选集合（本地 state）。 */
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const handleToggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  /** 模拟结果（memo 化）。 */
  const result = useMemo(
    () =>
      simulate({
        selected: Array.from(selected),
        requirements: data.requirements,
        composition: data.composition,
        baseSnapshot: activeVersion,
        denominator,
      }),
    [selected, data.requirements, data.composition, activeVersion, denominator, simulate]
  );

  const isSimulated = selected.size > 0;
  const evrDelta = result.evr - evr;

  return (
    <div className="flex flex-col gap-4">
      {/* —— 模拟结果条 —— */}
      <section
        className={[
          'mj-panel p-4',
          result.redlineTouched ? 'border-alert/60' : isSimulated ? 'border-improve/40' : '',
        ]
          .filter(Boolean)
          .join(' ')}
      >
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <div className="flex items-center gap-2">
            <TrendingUp size={16} className="text-improve" />
            <span className="mj-label">模拟结果（已勾选 {selected.size} 项需求）</span>
          </div>

          {/* 救回条数 */}
          <div className="flex flex-col">
            <span className="text-2xs text-text-muted">救回条数</span>
            <span className="mj-num text-lg font-semibold text-improve">
              +{result.rescued}
              <span className="ml-1 text-2xs text-text-muted">
                / 弃权 {activeVersion.abstained}
              </span>
            </span>
          </div>

          {/* EVR 现值→模拟值 */}
          <div className="flex flex-col">
            <span className="text-2xs text-text-muted">EVR</span>
            <span className="mj-num text-md text-text-primary">
              {(evr * 100).toFixed(1)}%
              <span className="mx-1.5 text-text-muted">→</span>
              <span className="font-semibold text-improve">
                {(result.evr * 100).toFixed(1)}%
              </span>
              {isSimulated && (
                <span className="ml-1.5 text-2xs text-improve-alt">
                  (+{(evrDelta * 100).toFixed(1)}pp)
                </span>
              )}
            </span>
          </div>

          {/* 风险加权 */}
          <div className="flex flex-col">
            <span className="text-2xs text-text-muted">造假风险加权</span>
            <span className="mj-num text-md text-improve-alt">
              {result.riskWeighted.toFixed(1)}
            </span>
          </div>

          {/* 重置 */}
          {isSimulated && (
            <button
              type="button"
              onClick={() => setSelected(new Set())}
              className="ml-auto inline-flex items-center gap-1.5 rounded-sm border border-border px-2 py-1 text-2xs text-text-secondary hover:text-text-primary"
            >
              <RotateCcw size={12} />
              重置
            </button>
          )}
        </div>

        {/* 红线告警 */}
        {result.redlineTouched && (
          <div className="mt-3 flex items-center gap-2 rounded-sm border border-alert/60 bg-alert/10 px-3 py-2">
            <AlertTriangle size={14} className="text-alert" />
            <span className="text-xs text-alert">
              红线告警：已勾选触及红线的需求 —— 该改动将违反「金额事实由宿主内核产生」原则，禁止实施。
            </span>
          </div>
        )}

        <p className="mt-3 text-2xs text-text-muted">
          说明：救回条数按 <span className="mj-num">linked_causes</span>{' '}
          去重防重复计数；风险加权 = Σ（救回条数 × 造假风险权重：无=0 / 低=0.1 / 中=0.3 / 高=0.6）。
          模拟仅用于评估杠杆，不代表承诺达成。
        </p>
      </section>

      {/* —— 需求池（P0/P1/P2 分组） —— */}
      {(['P0', 'P1', 'P2'] as Priority[]).map((p) => (
        <section key={p} className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <span className="mj-label">{PRIORITY_LABEL[p]}</span>
            <span className="text-2xs text-text-muted">
              {requirementsByPriority[p].length} 项
            </span>
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {requirementsByPriority[p].map((req) => (
              <RequirementCard
                key={req.id}
                requirement={req}
                selected={selected.has(req.id)}
                onToggle={handleToggle}
                highlighted={false}
              />
            ))}
          </div>
        </section>
      ))}

      {/* —— 不做清单（红区） —— */}
      <section className="mj-panel border-alert/30 p-4">
        <div className="mb-3 flex items-center gap-2">
          <ShieldX size={15} className="text-alert" />
          <span className="mj-label">不做清单（红区 · 不可为提升覆盖而触碰）</span>
          <Tag tone="alert">护城河</Tag>
        </div>
        <div className="flex flex-col gap-1">
          {data.redlines.map((r) => (
            <div
              key={r.id}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border/50 py-1.5 text-xs"
            >
              <span className="mj-num w-6 text-text-muted">{r.id}</span>
              <span className="text-alert">{r.forbidden}</span>
              <span className="text-text-muted">理由：{r.reason}</span>
              <span className="mj-num text-text-muted">涉及 {r.count} 条</span>
              <span className="mj-num ml-auto text-text-muted">{r.evidence_ref}</span>
            </div>
          ))}
        </div>
        <p className="mt-3 text-2xs text-alert/80">
          弃权是护城河，不是缺陷 —— 上述红线均为理念性约束，任何为「降低弃权率」而触碰红线的改动一律禁止。
        </p>
      </section>
    </div>
  );
}

export default SimulatorView;
