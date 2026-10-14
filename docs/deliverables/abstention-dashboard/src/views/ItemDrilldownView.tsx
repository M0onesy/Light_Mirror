import { useMemo, useState } from 'react';
import { Filter, X } from 'lucide-react';
import { useDiagnosis, X_CODE_ORDER } from '@/hooks/useDiagnosis';
import AmendmentBadge from '@/components/AmendmentBadge';
import Tag from '@/components/ui/Tag';
import type { XCode } from '@/types/diagnosis';

/**
 * 逐条归因下钻视图 —— 143 条表（按 activeXCode / cause 筛选，分页）。
 *
 * 逐条展示 page/seq/raw_label/x_code/gap_kind/cause/amendability/evidence_ref/关联需求。
 * 点击某行可关联需求 → dispatch SET_REQUIREMENT。
 */

export interface ItemDrilldownViewProps {
  onGoToSimulator: () => void;
}

const PAGE_SIZE = 25;

/** 逐条下钻视图组件。 */
export function ItemDrilldownView({ onGoToSimulator }: ItemDrilldownViewProps) {
  const { data, filteredItems, activeXCode, dispatch } = useDiagnosis();
  const [causeFilter, setCauseFilter] = useState<string>('');
  const [page, setPage] = useState(0);

  /** 可选 cause 列表。 */
  const causes = useMemo(
    () => Array.from(new Set(data.items.map((i) => i.cause))).sort(),
    [data.items]
  );

  /** 应用 cause 二次筛选。 */
  const rows = useMemo(() => {
    const base = causeFilter === '' ? filteredItems : filteredItems.filter((i) => i.cause === causeFilter);
    return base;
  }, [filteredItems, causeFilter]);

  /** 分页切片。 */
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageRows = rows.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  const handleXCodeClick = (code: XCode) => {
    dispatch({ type: 'SET_XCODE', code: activeXCode === code ? null : code });
    setPage(0);
  };

  const handleRowRequirement = (reqId: string | null) => {
    if (reqId !== null) {
      dispatch({ type: 'SET_REQUIREMENT', id: reqId });
    }
  };

  /** 各 X 码在 items 中的分布计数。 */
  const xCodeCounts = useMemo(() => {
    const m = new Map<XCode, number>();
    for (const item of data.items) {
      m.set(item.x_code, (m.get(item.x_code) ?? 0) + 1);
    }
    return m;
  }, [data.items]);

  return (
    <div className="flex flex-col gap-3">
      {/* —— 筛选工具栏 —— */}
      <section className="mj-panel flex flex-wrap items-center gap-x-4 gap-y-2 p-3">
        <div className="flex items-center gap-2">
          <Filter size={13} className="text-text-muted" />
          <span className="mj-label">按 X 码</span>
          {X_CODE_ORDER.filter((c) => (xCodeCounts.get(c) ?? 0) > 0).map((code) => {
            const active = activeXCode === code;
            const isRedline = data.x_code_defs.find((d) => d.code === code)?.is_redline === true;
            return (
              <button
                key={code}
                type="button"
                onClick={() => handleXCodeClick(code)}
                className={[
                  'mj-num rounded-sm border px-2 py-0.5 text-2xs transition-colors',
                  active
                    ? 'border-improve bg-improve/15 text-improve'
                    : isRedline
                      ? 'border-abstain/40 text-abstain hover:bg-abstain/10'
                      : 'border-border text-text-secondary hover:text-text-primary',
                ].join(' ')}
              >
                {code} · {xCodeCounts.get(code) ?? 0}
              </button>
            );
          })}
          {activeXCode !== null && (
            <button
              type="button"
              onClick={() => {
                dispatch({ type: 'SET_XCODE', code: null });
                setPage(0);
              }}
              className="inline-flex items-center gap-1 text-2xs text-text-muted hover:text-text-primary"
            >
              <X size={11} />
              清除
            </button>
          )}
        </div>

        <div className="flex items-center gap-2">
          <span className="mj-label">按原因</span>
          <select
            value={causeFilter}
            onChange={(e) => {
              setCauseFilter(e.target.value);
              setPage(0);
            }}
            className="rounded-sm border border-border bg-panel-alt px-2 py-1 text-2xs text-text-secondary outline-none focus:border-improve/60"
          >
            <option value="">全部（{data.items.length} 条样例）</option>
            {causes.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>

        <span className="ml-auto text-2xs text-text-muted">
          命中 <span className="mj-num text-text-secondary">{rows.length}</span> 条
          {activeXCode !== null && ` · 当前 ${activeXCode}`}
        </span>
      </section>

      {/* —— 下钻表 —— */}
      <section className="mj-panel overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b border-border text-text-muted">
                <th className="px-3 py-2 text-left font-medium">ID</th>
                <th className="px-3 py-2 text-right font-medium">页码</th>
                <th className="px-3 py-2 text-right font-medium">序号</th>
                <th className="px-3 py-2 text-left font-medium">原始标签</th>
                <th className="px-3 py-2 text-left font-medium">X 码</th>
                <th className="px-3 py-2 text-left font-medium">GAP_KIND</th>
                <th className="px-3 py-2 text-left font-medium">原因</th>
                <th className="px-3 py-2 text-left font-medium">可改善性</th>
                <th className="px-3 py-2 text-left font-medium">证据</th>
                <th className="px-3 py-2 text-left font-medium">关联需求</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((item) => {
                const isRedline = data.x_code_defs.find((d) => d.code === item.x_code)?.is_redline === true;
                return (
                  <tr
                    key={item.id}
                    className="border-b border-border/50 hover:bg-white/[0.02]"
                  >
                    <td className="mj-num px-3 py-1.5 text-text-muted">{item.id}</td>
                    <td className="mj-num px-3 py-1.5 text-right text-text-secondary">
                      {item.page}
                    </td>
                    <td className="mj-num px-3 py-1.5 text-right text-text-secondary">
                      {item.seq}
                    </td>
                    <td className="px-3 py-1.5 text-text-primary">{item.raw_label}</td>
                    <td className="px-3 py-1.5">
                      <Tag tone={isRedline ? 'abstain' : 'improve'} numeric>
                        {item.x_code}
                      </Tag>
                    </td>
                    <td className="mj-num px-3 py-1.5 text-text-secondary">
                      {item.gap_kind}
                    </td>
                    <td className="px-3 py-1.5 text-text-secondary">{item.cause}</td>
                    <td className="px-3 py-1.5">
                      <AmendmentBadge amendability={item.amendability} compact />
                    </td>
                    <td className="mj-num px-3 py-1.5 text-text-muted">
                      {item.evidence_ref}
                    </td>
                    <td className="px-3 py-1.5">
                      {item.requirement_ref !== null ? (
                        <button
                          type="button"
                          onClick={() => handleRowRequirement(item.requirement_ref)}
                          className="mj-num text-improve hover:underline"
                        >
                          {item.requirement_ref}
                        </button>
                      ) : (
                        <span className="text-text-muted">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
              {pageRows.length === 0 && (
                <tr>
                  <td colSpan={10} className="px-3 py-8 text-center text-text-muted">
                    无匹配条目，请调整筛选条件。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* —— 分页 —— */}
        <div className="flex items-center justify-between border-t border-border px-3 py-2 text-2xs">
          <span className="text-text-muted">
            第 <span className="mj-num text-text-secondary">{safePage + 1}</span> /{' '}
            <span className="mj-num text-text-secondary">{pageCount}</span> 页
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={safePage === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              className="rounded-sm border border-border px-2 py-1 text-text-secondary disabled:opacity-40 enabled:hover:text-text-primary"
            >
              上一页
            </button>
            <button
              type="button"
              disabled={safePage >= pageCount - 1}
              onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
              className="rounded-sm border border-border px-2 py-1 text-text-secondary disabled:opacity-40 enabled:hover:text-text-primary"
            >
              下一页
            </button>
            <button
              type="button"
              onClick={onGoToSimulator}
              className="ml-2 rounded-sm border border-improve/40 bg-improve/10 px-2 py-1 text-improve hover:bg-improve/20"
            >
              去需求模拟器
            </button>
          </div>
        </div>
      </section>

      <p className="text-2xs text-text-muted">
        注：表内为占位样例（{data.items.length} 条），真实 143 条首因分组已按「构成分解」逐因展开；完整清单由离线脚本导入。
      </p>
    </div>
  );
}

export default ItemDrilldownView;
