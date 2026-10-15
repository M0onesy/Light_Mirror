import { scaleLinear } from 'd3-scale';
import type { Amendability } from '@/types/diagnosis';
import { tokens, ON_FILL_TEXT } from '@/theme/tokens';

/**
 * 弃权构成水平堆叠条（手写 SVG）。
 *
 * 着色语义（核心叙事红线）：
 * - amendable / amendable_partial → 深青 / 深琥珀（可改善空间）
 * - needs_user_declaration → 深紫
 * - intentional → 中性冷灰（合法弃权，被尊重，绝不用红）
 *
 * 取色全部来自 `@/theme/tokens`（浅色专底下可读），禁止字面量。
 */

export interface AbstentionSegment {
  id: string;
  label: string;
  count: number;
  amendability: Amendability;
}

export interface AbstentionBarProps {
  segments: AbstentionSegment[];
  total: number;
  onSegmentClick?: (id: string) => void;
  activeId?: string | null;
}

/** amendability → 填充色（浅底可读，取自 tokens）。 */
const FILL: Record<Amendability, string> = {
  amendable: tokens.improve,
  amendable_partial: tokens.improveAlt,
  needs_user_declaration: tokens.declare,
  intentional: tokens.abstain,
};

/** 条高（px）。 */
const BAR_H = 40;

/** 构成水平堆叠条组件。 */
export function AbstentionBar({
  segments,
  total,
  onSegmentClick,
  activeId = null,
}: AbstentionBarProps) {
  const width = 1000; // viewBox 宽度，SVG 自适应
  const sum = segments.reduce((acc, s) => acc + s.count, 0);
  const denom = total > 0 ? total : sum > 0 ? sum : 1;

  // d3-scale 计算每段 x 起点与宽度
  const x = scaleLinear().domain([0, denom]).range([0, width]);
  let cursor = 0;

  return (
    <div className="flex flex-col gap-2">
      <svg
        viewBox={`0 0 ${width} ${BAR_H}`}
        preserveAspectRatio="none"
        className="w-full"
        style={{ height: `${BAR_H}px` }}
        role="img"
        aria-label="弃权构成水平堆叠条"
      >
        {segments.map((seg) => {
          const x0 = x(cursor);
          const x1 = x(cursor + seg.count);
          cursor += seg.count;
          const w = Math.max(x1 - x0, 0);
          const active = activeId === seg.id;
          return (
            <g
              key={seg.id}
              onClick={() => onSegmentClick?.(seg.id)}
              style={{ cursor: onSegmentClick !== undefined ? 'pointer' : 'default' }}
            >
              <rect
                x={x0}
                y={active ? 3 : 6}
                width={w}
                height={active ? BAR_H - 6 : BAR_H - 12}
                fill={FILL[seg.amendability]}
                opacity={active || activeId === null ? 1 : 0.55}
              />
              {/* 段内标签（宽度足够才显示） */}
              {w > 60 && (
                <text
                  x={x0 + 8}
                  y={BAR_H / 2 + 4}
                  fill={ON_FILL_TEXT}
                  fontSize={13}
                  fontWeight={600}
                  style={{ fontVariantNumeric: 'tabular-nums' }}
                >
                  {seg.label} {seg.count}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {/* 图例行（含精确计数） */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        {segments.map((seg) => (
          <button
            key={seg.id}
            type="button"
            onClick={() => onSegmentClick?.(seg.id)}
            className={[
              'inline-flex items-center gap-1.5 text-2xs transition-opacity',
              onSegmentClick !== undefined ? 'hover:opacity-80' : '',
              activeId !== null && activeId !== seg.id ? 'opacity-50' : '',
            ]
              .filter(Boolean)
              .join(' ')}
          >
            <span
              className="inline-block h-2 w-2 rounded-sm"
              style={{ backgroundColor: FILL[seg.amendability] }}
            />
            <span className="text-text-secondary">{seg.label}</span>
            <span className="mj-num text-text-primary">{seg.count}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export default AbstentionBar;
