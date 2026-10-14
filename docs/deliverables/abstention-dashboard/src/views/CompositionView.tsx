import { useMemo } from 'react';
import { useDiagnosis } from '@/hooks/useDiagnosis';
import AbstentionBar, { type AbstentionSegment } from '@/components/AbstentionBar';
import MigrationChart from '@/components/MigrationChart';
import GapKindLegend from '@/components/GapKindLegend';
import AmendmentBadge from '@/components/AmendmentBadge';
import Tag from '@/components/ui/Tag';
import type { Amendability } from '@/types/diagnosis';

/**
 * 构成分解视图 —— 弃权构成分解 + X 码迁移趋势（04→08）。
 *
 * 内容：堆叠条 + 10 条 cause 明细表 + 迁移图 + GAP_KIND 图例。
 * 点击构成段 → dispatch SET_XCODE（供逐条下钻联动）。
 */

export interface CompositionViewProps {
  onDrillIntoXCode: () => void;
}

/** 明细表列（高密度）。 */
export function CompositionView({ onDrillIntoXCode }: CompositionViewProps) {
  const {
    data,
    activeVersion,
    compareVersion,
    compositionByAmendability,
    dispatch,
    activeXCode,
  } = useDiagnosis();

  const segments = useMemo<AbstentionSegment[]>(
    () =>
      compositionByAmendability.map((s) => ({
        id: s.amendability,
        label: s.label,
        count: s.count,
        amendability: s.amendability as Amendability,
      })),
    [compositionByAmendability]
  );

  /** 按 count 降序排列的构成明细。 */
  const sortedComposition = useMemo(
    () => [...data.composition].sort((a, b) => b.count - a.count),
    [data.composition]
  );

  /** 点击构成段 → 选中最主要的 X 码（该 amendability 下 count 最大的 cause 的 x_code）。 */
  const handleSegmentClick = (amendability: string) => {
    const candidates = data.composition.filter(
      (c) => c.amendability === amendability
    );
    if (candidates.length === 0) {
      return;
    }
    const top = candidates.reduce((a, b) => (b.count > a.count ? b : a));
    dispatch({ type: 'SET_XCODE', code: activeXCode === top.x_code ? null : top.x_code });
  };

  return (
    <div className="flex flex-col gap-4">
      {/* —— 构成堆叠条 —— */}
      <section className="mj-panel flex flex-col gap-3 p-4">
        <div className="flex items-center justify-between">
          <span className="mj-label">弃权构成分解（313 条按可改善性）</span>
          <span className="text-2xs text-text-muted">
            点击色段可联动过滤 X 码 {activeXCode !== null ? `· 当前 ${activeXCode}` : ''}
          </span>
        </div>
        <AbstentionBar
          segments={segments}
          total={activeVersion.abstained}
          onSegmentClick={handleSegmentClick}
          activeId={null}
        />
        {activeXCode !== null && (
          <button
            type="button"
            onClick={onDrillIntoXCode}
            className="self-start text-2xs text-improve hover:underline"
          >
            查看 {activeXCode} 下逐条归因 →
          </button>
        )}
      </section>

      {/* —— 10 条 cause 明细表 —— */}
      <section className="mj-panel overflow-hidden">
        <div className="mj-divider flex items-center justify-between px-4 py-2">
          <span className="mj-label">逐因明细（{data.composition.length} 条首因分组 · 合计 143）</span>
          <span className="text-2xs text-text-muted">来源 ABSTENTION_SMALL_SAMPLE.md</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b border-border text-text-muted">
                <th className="px-3 py-2 text-left font-medium">ID</th>
                <th className="px-3 py-2 text-left font-medium">首要问题</th>
                <th className="px-3 py-2 text-right font-medium">条数</th>
                <th className="px-3 py-2 text-left font-medium">X 码</th>
                <th className="px-3 py-2 text-left font-medium">GAP_KIND</th>
                <th className="px-3 py-2 text-left font-medium">可改善性</th>
                <th className="px-3 py-2 text-left font-medium">证据</th>
              </tr>
            </thead>
            <tbody>
              {sortedComposition.map((c) => (
                <tr
                  key={c.id}
                  className="border-b border-border/50 hover:bg-white/[0.02]"
                >
                  <td className="mj-num px-3 py-1.5 text-text-muted">{c.id}</td>
                  <td className="px-3 py-1.5 text-text-primary">{c.cause}</td>
                  <td className="mj-num px-3 py-1.5 text-right text-text-primary">
                    {c.count}
                  </td>
                  <td className="px-3 py-1.5">
                    <Tag
                      tone={c.amendability === 'intentional' ? 'abstain' : 'improve'}
                      numeric
                    >
                      {c.x_code}
                    </Tag>
                  </td>
                  <td className="mj-num px-3 py-1.5 text-text-secondary">
                    {c.gap_kind}
                  </td>
                  <td className="px-3 py-1.5">
                    <AmendmentBadge amendability={c.amendability} compact />
                  </td>
                  <td className="px-3 py-1.5 text-text-muted">{c.evidence_ref}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-border">
                <td className="px-3 py-2" />
                <td className="px-3 py-2 text-text-secondary">合计</td>
                <td className="mj-num px-3 py-2 text-right font-semibold text-text-primary">
                  {sortedComposition.reduce((acc, c) => acc + c.count, 0)}
                </td>
                <td className="px-3 py-2" colSpan={4} />
              </tr>
            </tfoot>
          </table>
        </div>
      </section>

      {/* —— X 码迁移图 + 图例 —— */}
      <section className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <div className="mj-panel p-4 lg:col-span-2">
          <MigrationChart from={activeVersion} to={compareVersion} />
        </div>
        <div className="mj-panel p-4">
          <GapKindLegend
            gapKinds={data.gap_kind_defs}
            xCodes={data.x_code_defs}
          />
        </div>
      </section>
    </div>
  );
}

export default CompositionView;
