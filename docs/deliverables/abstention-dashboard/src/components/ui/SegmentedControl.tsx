/**
 * 分段控件 —— 顶栏五视图切换（替代路由）。
 * 终端气质：方角、细分隔、选中态用一条强调底线。
 * 窄屏（DASH-5）：容器可横向滚动、不换行，避免 375px 下崩坏。
 */

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  /** 可选：右侧计数徽标（等宽数字）。 */
  badge?: string | number;
}

export interface SegmentedControlProps<T extends string> {
  options: SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
}

/** 分段控件组件。 */
export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  className = '',
}: SegmentedControlProps<T>) {
  return (
    <div
      role="tablist"
      className={['inline-flex items-stretch gap-0 overflow-x-auto whitespace-nowrap max-w-full', className]
        .filter(Boolean)
        .join(' ')}
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            role="tab"
            aria-selected={active}
            type="button"
            onClick={() => onChange(opt.value)}
            className={[
              'relative px-3 py-1.5 text-sm font-medium transition-colors',
              'border-b-2 -mb-px whitespace-nowrap',
              active
                ? 'text-text-primary border-improve'
                : 'text-text-muted border-transparent hover:text-text-secondary',
            ].join(' ')}
          >
            {opt.label}
            {opt.badge !== undefined && (
              <span className="ml-1.5 mj-num text-2xs text-text-muted">
                {opt.badge}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export default SegmentedControl;
