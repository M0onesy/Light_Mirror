import type { ReactNode } from 'react';
import { ShieldCheck, ExternalLink } from 'lucide-react';
import type { DiagnosisMeta, ViewKey } from '@/types/diagnosis';
import SegmentedControl, { type SegmentedOption } from './ui/SegmentedControl';
import Tag from './ui/Tag';
import { CLARIFIER_URL } from '@/utils/clarifierLink';

/**
 * 应用外壳 —— 顶栏 + 分段控件 + 页脚（设计气质载体）。
 *
 * 页脚含核心叙事红线声明：「弃权是护城河，不是缺陷」。
 */

export interface LayoutProps {
  view: ViewKey;
  onViewChange: (v: ViewKey) => void;
  meta: DiagnosisMeta;
  children: ReactNode;
}

const VIEW_OPTIONS: SegmentedOption<ViewKey>[] = [
  { value: 'overview', label: '结论总览' },
  { value: 'composition', label: '构成分解' },
  { value: 'rootcause', label: '根因树' },
  { value: 'drilldown', label: '逐条下钻' },
  { value: 'simulator', label: '需求模拟器' },
];

/** 布局外壳组件。 */
export function Layout({ view, onViewChange, meta, children }: LayoutProps) {
  return (
    <div className="flex min-h-full flex-col bg-base">
      {/* 顶栏 */}
      <header className="sticky top-0 z-10 border-b border-border bg-base/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          {/* 标题块 */}
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-sm border border-improve/40 bg-improve/10">
              <ShieldCheck size={18} className="text-improve" />
            </div>
            <div className="flex flex-col leading-tight">
              <h1 className="text-md font-semibold text-text-primary">
                明镜 MingJing · 弃权率归因诊断看板
              </h1>
              <span className="text-2xs text-text-muted">
                研报 {meta.report_pages} 页 · 基线 {meta.report_hash}
              </span>
            </div>
          </div>

          {/* 版本标签 */}
          <div className="flex items-center gap-2">
            <Tag tone="improve" numeric title="当前主版本">
              主 {meta.primary_version}
            </Tag>
            <Tag tone="neutral" numeric title="迁移对比版本">
              对比 {meta.compare_version}
            </Tag>
          </div>

          {/* 口径澄清工具外链（DASH-7 双向可达） */}
          <a
            href={CLARIFIER_URL}
            target="_blank"
            rel="noopener"
            className="inline-flex items-center gap-1 text-2xs text-accent hover:underline"
            title="在新标签打开口径澄清工具"
          >
            <ExternalLink size={12} />
            口径澄清工具
          </a>

          {/* 分段控件（右对齐；窄屏需可收缩，见 DASH-5） */}
          <div className="ml-auto min-w-0 max-w-full">
            <SegmentedControl
              options={VIEW_OPTIONS}
              value={view}
              onChange={onViewChange}
            />
          </div>
        </div>
      </header>

      {/* 主内容区 */}
      <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-4">
        {children}
      </main>

      {/* 页脚 —— 核心叙事红线声明 */}
      <footer className="border-t border-border bg-panel-alt">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-x-6 gap-y-1 px-4 py-3">
          <p className="text-2xs font-semibold tracking-wide text-accent">
            弃权是护城河，不是缺陷 —— 本看板不追求「降低弃权率」，只追求「提升有效核实率 / 扩大可核实覆盖」。
          </p>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-text-muted">
            <a
              href={CLARIFIER_URL}
              target="_blank"
              rel="noopener"
              className="inline-flex items-center gap-1 text-accent hover:underline"
            >
              <ExternalLink size={11} />
              打开口径澄清工具
            </a>
            <span>·</span>
            <span>口径 {meta.denominator_scope}</span>
            <span className="mj-num">生成 {meta.generated_at}</span>
          </div>
        </div>
        <div className="mx-auto max-w-[1400px] px-4 pb-2 text-2xs text-text-muted">
          若外链无法打开，请在同一静态服务器下访问 docs/deliverables/（如
          scope-clarifier/index.html 无法打开，可直接在浏览器打开该文件）。
        </div>
      </footer>
    </div>
  );
}

export default Layout;
