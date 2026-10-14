import { ChevronRight, ChevronDown, CircleDot, ShieldCheck } from 'lucide-react';
import type { RootCauseNode } from '@/types/diagnosis';

/**
 * 根因树递归组件 —— 可折叠 + 叶子联动 activeRequirement 高亮。
 */

export interface RootCauseTreeProps {
  node: RootCauseNode;
  depth?: number;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  activeRequirement?: string | null;
  /** 当前选中的叶子 id（用于详情联动）。 */
  activeLeaf?: string | null;
  onSelectLeaf?: (node: RootCauseNode) => void;
}

/** X 码 → 左侧色条颜色（neutral 冷灰=保守，高亮=可改善）。 */
function accentColor(node: RootCauseNode): string {
  if (node.redline === true) {
    return '#6B7280';
  }
  switch (node.x_code) {
    case 'X3':
      return '#38BDF8';
    case 'X6':
      return '#F59E0B';
    case 'X2':
      return '#6B7280';
    case 'X1':
    case 'X5':
      return '#38BDF8';
    default:
      return '#4B5563';
  }
}

/** 根因树节点（递归）。 */
export function RootCauseTree({
  node,
  depth = 0,
  expanded,
  onToggle,
  activeRequirement = null,
  activeLeaf = null,
  onSelectLeaf,
}: RootCauseTreeProps) {
  const hasChildren = node.children !== undefined && node.children.length > 0;
  const isExpanded = expanded.has(node.id);
  const isLeaf = node.leaf === true || !hasChildren;
  const linked = activeRequirement !== null && node.requirement_ref === activeRequirement;
  const selected = activeLeaf === node.id;

  return (
    <div className="select-none">
      <div
        className={[
          'group flex items-center gap-2 py-1 pr-2 rounded-sm transition-colors',
          linked ? 'bg-improve/10' : selected ? 'bg-white/5' : '',
        ]
          .filter(Boolean)
          .join(' ')}
        style={{ paddingLeft: `${depth * 16 + 4}px` }}
      >
        {/* 展开/折叠箭头 */}
        {hasChildren ? (
          <button
            type="button"
            onClick={() => onToggle(node.id)}
            className="flex h-4 w-4 items-center justify-center text-text-muted hover:text-text-primary"
            aria-label={isExpanded ? '折叠' : '展开'}
          >
            {isExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </button>
        ) : (
          <span className="flex h-4 w-4 items-center justify-center text-text-muted">
            <CircleDot size={10} />
          </span>
        )}

        {/* 色条 */}
        <span
          className="inline-block h-3 w-1 rounded-sm"
          style={{ backgroundColor: accentColor(node) }}
        />

        {/* 标签（可点击叶子联动详情） */}
        <button
          type="button"
          disabled={!isLeaf || onSelectLeaf === undefined}
          onClick={() => isLeaf && onSelectLeaf?.(node)}
          className={[
            'flex-1 text-left text-sm truncate',
            isLeaf ? 'cursor-pointer' : 'cursor-default',
            linked ? 'text-text-primary font-medium' : 'text-text-secondary',
          ]
            .filter(Boolean)
            .join(' ')}
          title={node.label}
        >
          {node.label}
        </button>

        {/* 红线标记 */}
        {node.redline === true && (
          <span
            className="inline-flex items-center gap-1 text-2xs text-abstain"
            title="有意保守·护城河，不可降"
          >
            <ShieldCheck size={11} />
            护城河
          </span>
        )}

        {/* 需求关联标记 */}
        {node.requirement_ref !== undefined && node.requirement_ref !== null && (
          <span
            className={[
              'mj-num rounded-sm px-1 py-0.5 text-2xs',
              linked ? 'bg-improve/20 text-improve' : 'text-text-muted',
            ].join(' ')}
          >
            {node.requirement_ref}
          </span>
        )}

        {/* 计数 */}
        <span className="mj-num w-10 text-right text-sm text-text-primary">
          {node.count}
        </span>
      </div>

      {/* 子节点 */}
      {hasChildren && isExpanded && (
        <div className="border-l border-border/60" style={{ marginLeft: `${depth * 16 + 12}px` }}>
          {node.children?.map((child) => (
            <RootCauseTree
              key={child.id}
              node={child}
              depth={0}
              expanded={expanded}
              onToggle={onToggle}
              activeRequirement={activeRequirement}
              activeLeaf={activeLeaf}
              onSelectLeaf={onSelectLeaf}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default RootCauseTree;
