import { scaleLinear } from 'd3-scale';
import { ArrowRight } from 'lucide-react';
import type { VersionSnapshot, XCode } from '@/types/diagnosis';

/**
 * X 码迁移图（stable-04 → stable-08）。
 *
 * 手写 SVG 双环/箭头：左侧为 from 版本、右侧为 to 版本，
 * 用带宽与箭头展示各 X 码的迁移（叙事：X6 ↓ / X3 ↑，根因重心位移而非消失）。
 */

export interface MigrationChartProps {
  from: VersionSnapshot;
  to: VersionSnapshot;
}

/** 展示顺序与配色（X3 定义=青，X6 解析=琥珀，X2 前提=中性灰）。 */
const CODES: XCode[] = ['X3', 'X6', 'X2'];
const COLOR: Record<string, string> = {
  X3: '#38BDF8',
  X6: '#F59E0B',
  X2: '#6B7280',
};
const NAME: Record<string, string> = {
  X3: '定义缺口',
  X6: '主体/维度待确认',
  X2: '公式前提不成立',
};

/** 迁移图组件。 */
export function MigrationChart({ from, to }: MigrationChartProps) {
  const width = 640;
  const height = 200;
  const labelW = 130;
  const barMaxW = 260;
  const lg = 12;

  const maxVal = Math.max(
    ...CODES.map((c) => Math.max(from.x_codes[c] ?? 0, to.x_codes[c] ?? 0)),
    1
  );
  const scale = scaleLinear().domain([0, maxVal]).range([0, barMaxW]);
  const rowH = height / CODES.length;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-2xs text-text-muted">
          版本迁移对比（根因重心位移而非消失）
        </span>
        <span className="mj-num text-2xs text-text-secondary">
          {from.label} <ArrowRight size={10} className="inline" /> {to.label}
        </span>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        role="img"
        aria-label="X 码迁移对比图"
      >
        {CODES.map((code, idx) => {
          const cy = idx * rowH + rowH / 2;
          const fv = from.x_codes[code] ?? 0;
          const tv = to.x_codes[code] ?? 0;
          const fw = scale(fv);
          const tw = scale(tv);
          const color = COLOR[code];
          const delta = tv - fv;
          const deltaColor = delta > 0 ? '#38BDF8' : delta < 0 ? '#F59E0B' : '#6B7280';
          return (
            <g key={code}>
              {/* 行分隔线 */}
              <line
                x1={0}
                y1={idx * rowH}
                x2={width}
                y2={idx * rowH}
                stroke="#1F2937"
                strokeWidth={1}
              />
              {/* 左侧标签 */}
              <text x={0} y={cy - 4} fill="#9CA3AF" fontSize={11}>
                {code}
              </text>
              <text x={0} y={cy + 9} fill="#6B7280" fontSize={9}>
                {NAME[code]}
              </text>
              {/* from 条 */}
              <rect
                x={labelW}
                y={cy - lg / 2}
                width={fw}
                height={lg}
                fill={color}
                opacity={0.5}
              />
              <text
                x={labelW + fw + 6}
                y={cy + 3}
                fill="#9CA3AF"
                fontSize={10}
                style={{ fontVariantNumeric: 'tabular-nums' }}
              >
                {fv}
              </text>
              {/* 中部箭头 */}
              <g transform={`translate(${labelW + barMaxW + 20}, ${cy})`}>
                <line x1={0} y1={0} x2={26} y2={0} stroke={deltaColor} strokeWidth={1.5} />
                <polygon
                  points="26,-4 34,0 26,4"
                  fill={deltaColor}
                  transform={`rotate(${delta === 0 ? 0 : delta > 0 ? 0 : 0})`}
                />
              </g>
              {/* to 条 */}
              <rect
                x={labelW + barMaxW + 60}
                y={cy - lg / 2}
                width={tw}
                height={lg}
                fill={color}
              />
              <text
                x={labelW + barMaxW + 60 + tw + 6}
                y={cy + 3}
                fill="#E5E7EB"
                fontSize={10}
                style={{ fontVariantNumeric: 'tabular-nums' }}
              >
                {tv}
              </text>
              {/* delta 徽标 */}
              <text
                x={width - 2}
                y={cy + 3}
                textAnchor="end"
                fill={deltaColor}
                fontSize={11}
                fontWeight={600}
                style={{ fontVariantNumeric: 'tabular-nums' }}
              >
                {delta > 0 ? `+${delta}` : delta === 0 ? '0' : `${delta}`}
              </text>
            </g>
          );
        })}
        <line x1={0} y1={height} x2={width} y2={height} stroke="#1F2937" strokeWidth={1} />
      </svg>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-2xs text-text-muted">
        <span>
          弃权总数 <span className="mj-num text-text-secondary">{from.abstained} → {to.abstained}</span>
        </span>
        <span className="text-improve-alt">
          X6 解析不足下降{' '}
          <span className="mj-num">
            {(from.x_codes.X6 ?? 0)} → {(to.x_codes.X6 ?? 0)}（{(to.x_codes.X6 ?? 0) - (from.x_codes.X6 ?? 0)}）
          </span>
        </span>
        <span className="text-improve">
          X3 定义缺口上升{' '}
          <span className="mj-num">
            {(from.x_codes.X3 ?? 0)} → {(to.x_codes.X3 ?? 0)}（+{(to.x_codes.X3 ?? 0) - (from.x_codes.X3 ?? 0)}）
          </span>
        </span>
      </div>
    </div>
  );
}

export default MigrationChart;
