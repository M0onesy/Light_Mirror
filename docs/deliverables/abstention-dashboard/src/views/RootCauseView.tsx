import { useMemo, useState } from 'react';
import { ArrowRight } from 'lucide-react';
import { useDiagnosis } from '@/hooks/useDiagnosis';
import RootCauseTree from '@/components/RootCauseTree';
import AmendmentBadge, { RedlineBadge } from '@/components/AmendmentBadge';
import Tag from '@/components/ui/Tag';
import type { RootCauseNode } from '@/types/diagnosis';

/**
 * 根因树视图 —— 可折叠 + 联动高亮。
 *
 * 左：可折叠根因树（默认展开全部一级节点）。
 * 右：选中叶子的详情（证据 ref、关联需求、可行动性、护城河标记）。
 */

export interface RootCauseViewProps {
  onGoToSimulator: () => void;
}

/** 递归收集所有节点 id（用于默认展开一级）。 */
function collectIds(node: RootCauseNode, acc: string[] = []): string[] {
  acc.push(node.id);
  for (const child of node.children ?? []) {
    collectIds(child, acc);
  }
  return acc;
}

/** 递归扁平化所有叶子节点。 */
function collectLeaves(node: RootCauseNode, acc: RootCauseNode[] = []): RootCauseNode[] {
  if (node.children === undefined || node.children.length === 0) {
    acc.push(node);
  } else {
    for (const child of node.children) {
      collectLeaves(child, acc);
    }
  }
  return acc;
}

/** 根因树视图组件。 */
export function RootCauseView({ onGoToSimulator }: RootCauseViewProps) {
  const { data, dispatch } = useDiagnosis();

  const tree = data.root_cause_tree;

  /** 默认展开一级子节点（root + 三大分支）。 */
  const [expanded, setExpanded] = useState<Set<string>>(() => {
    const initial = new Set<string>();
    initial.add(tree.id);
    for (const child of tree.children ?? []) {
      initial.add(child.id);
    }
    return initial;
  });

  /** 当前选中的叶子。 */
  const [activeLeafId, setActiveLeafId] = useState<string | null>(null);

  const handleToggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const leaves = useMemo(() => collectLeaves(tree), [tree]);
  const activeLeaf = useMemo(
    () => leaves.find((l) => l.id === activeLeafId) ?? null,
    [leaves, activeLeafId]
  );

  /** 关联需求对象。 */
  const linkedRequirement = useMemo(() => {
    if (activeLeaf?.requirement_ref === null || activeLeaf?.requirement_ref === undefined) {
      return null;
    }
    return data.requirements.find((r) => r.id === activeLeaf.requirement_ref) ?? null;
  }, [activeLeaf, data.requirements]);

  /** 选中叶子时同步高亮需求（供跨视图联动）。 */
  const handleSelectLeaf = (node: RootCauseNode) => {
    setActiveLeafId(node.id);
    dispatch({ type: 'SET_REQUIREMENT', id: node.requirement_ref ?? null });
  };

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      {/* —— 左：根因树 —— */}
      <section className="mj-panel p-4 lg:col-span-2">
        <div className="mb-3 flex items-center justify-between">
          <span className="mj-label">根因树（可行动叶子节点）</span>
          <span className="text-2xs text-text-muted">
            {leaves.length} 个叶子 · 共 {collectIds(tree).length} 节点
          </span>
        </div>
        <RootCauseTree
          node={tree}
          expanded={expanded}
          onToggle={handleToggle}
          activeRequirement={activeLeaf?.requirement_ref ?? null}
          activeLeaf={activeLeafId}
          onSelectLeaf={handleSelectLeaf}
        />
      </section>

      {/* —— 右：叶子详情 —— */}
      <section className="mj-panel flex flex-col gap-3 p-4">
        <span className="mj-label">叶子详情</span>
        {activeLeaf === null ? (
          <p className="text-xs text-text-muted">
            点击左侧任一片叶子，查看其证据与关联需求。
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <span className="mj-num text-2xs text-text-muted">
                  {activeLeaf.id}
                </span>
                {activeLeaf.x_code !== undefined && (
                  <Tag
                    tone={activeLeaf.redline === true ? 'abstain' : 'improve'}
                    numeric
                  >
                    {activeLeaf.x_code}
                  </Tag>
                )}
                {activeLeaf.redline === true && <RedlineBadge label="护城河" />}
              </div>
              <div className="text-sm font-medium text-text-primary">
                {activeLeaf.label}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2 text-2xs">
              <div className="flex flex-col">
                <span className="text-text-muted">条数</span>
                <span className="mj-num text-md text-text-primary">
                  {activeLeaf.count}
                </span>
              </div>
              <div className="flex flex-col">
                <span className="text-text-muted">可行动</span>
                <span
                  className={
                    activeLeaf.actionable === true ? 'text-improve' : 'text-text-secondary'
                  }
                >
                  {activeLeaf.actionable === true ? '是' : '否（保守）'}
                </span>
              </div>
            </div>

            <div className="flex flex-col gap-1 border-t border-border/60 pt-2">
              <span className="text-2xs text-text-muted">证据来源</span>
              <span className="mj-num text-xs text-text-secondary">
                {activeLeaf.evidence_ref ?? '—'}
              </span>
            </div>

            {linkedRequirement !== null && (
              <div className="flex flex-col gap-2 border-t border-border/60 pt-2">
                <span className="text-2xs text-text-muted">关联需求</span>
                <div className="flex items-center gap-2">
                  <span className="mj-num text-xs text-improve">
                    {linkedRequirement.id}
                  </span>
                  <span className="text-xs text-text-primary">
                    {linkedRequirement.title}
                  </span>
                </div>
                <div className="flex items-center gap-2 text-2xs">
                  <AmendmentBadge amendability="amendable" compact />
                  <span className="text-text-muted">
                    救回 <span className="mj-num text-improve">{linkedRequirement.rescue_estimate}</span> 条
                  </span>
                </div>
                <button
                  type="button"
                  onClick={onGoToSimulator}
                  className="inline-flex items-center gap-1.5 self-start rounded-sm border border-improve/40 bg-improve/10 px-2 py-1 text-2xs text-improve hover:bg-improve/20"
                >
                  在模拟器中勾选 {linkedRequirement.id}
                  <ArrowRight size={12} />
                </button>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

export default RootCauseView;
