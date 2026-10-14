import type { GapKindDef, XCodeDef } from '@/types/diagnosis';
import Tag from './ui/Tag';

/**
 * GAP_KIND ↔ X 码 图例 —— 帮助读者理解 7 类根因类别与六码的映射关系。
 */

export interface GapKindLegendProps {
  gapKinds: GapKindDef[];
  xCodes: XCodeDef[];
}

/** X 码 → 色调（中性=保守，高亮=可改善）。 */
function toneOf(xCode: string | null): 'abstain' | 'improve' | 'neutral' {
  switch (xCode) {
    case 'X2':
    case 'X4':
      return 'abstain';
    case 'X1':
    case 'X3':
    case 'X5':
    case 'X6':
      return 'improve';
    default:
      return 'neutral';
  }
}

/** 图例组件。 */
export function GapKindLegend({ gapKinds, xCodes }: GapKindLegendProps) {
  const defMap = new Map(xCodes.map((d) => [d.code, d]));
  return (
    <div className="flex flex-col gap-2">
      <span className="mj-label">GAP_KIND ↔ X 码映射（7 类根因类别）</span>
      <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
        {gapKinds.map((gk) => {
          const def = gk.x_code !== null ? defMap.get(gk.x_code) : undefined;
          const tone = toneOf(gk.x_code);
          return (
            <div
              key={gk.kind}
              className="flex items-center justify-between gap-2 border-b border-border/50 py-1"
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className="mj-num text-2xs text-text-secondary w-28 shrink-0">
                  {gk.kind}
                </span>
                <span className="text-xs text-text-primary truncate" title={gk.desc}>
                  {gk.name}
                </span>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                {gk.x_code !== null && <Tag tone={tone} numeric>{gk.x_code}</Tag>}
                {def?.is_redline === true && (
                  <Tag tone="abstain" title="有意保守·护城河">
                    护城河
                  </Tag>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default GapKindLegend;
