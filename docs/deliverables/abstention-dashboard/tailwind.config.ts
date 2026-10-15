import type { Config } from 'tailwindcss';

/**
 * Tailwind 主题配置 —— 明镜诊断看板设计 token（浅色专业风）。
 *
 * 核心叙事约定（不可违反）：
 * - 合法弃权（X2/X4、premise/conflict）用**克制的冷灰/石板色**表达，绝不用错误红。
 * - 「可改善空间」用高亮色（深青/深琥珀）；「待用户声明」用深紫。
 * - 仅反指标 >0 或红线触碰才用红。
 *
 * 本文件与 `src/theme/tokens.ts` **同值同义**，后者是 SVG 取色的唯一 JS 来源。
 * 迁移依据：DESIGN-ui-optimization.md §2.1 / §2.2（逐条 20 项）。
 */
const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // 背景（浅色专业风）
        base: '#F5F7FA',
        panel: '#FFFFFF',
        'panel-alt': '#FAFBFD',
        border: '#DDE3EA',
        // 合法弃权（中性冷灰/石板色）—— 它们是被尊重的（绝不用红）
        abstain: '#64748B',
        'abstain-muted': '#7C8998',
        'abstain-dim': '#C9D2DC',
        // 可改善空间（浅底可读的深色高亮）
        improve: '#0E7490',
        'improve-alt': '#B45309',
        // 待用户声明
        declare: '#6D28D9',
        // 警戒（仅真正违规）
        alert: '#DC2626',
        'alert-soft': '#FEE2E2',
        // 强调蓝（链接/主按钮）
        accent: '#3B5B8C',
        // 提示（琥珀棕）
        warn: '#8A6D3B',
        // 命令块底（唯一保留的深底 —— 代码块惯例）
        cmd: '#0F172A',
        // 文本
        'text-primary': '#1F2A37',
        'text-secondary': '#5C6B7A',
        'text-muted': '#8593A3',
        // 合规绿（反指标=0）
        ok: '#15803D',
      },
      fontFamily: {
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          '"Segoe UI"',
          '"PingFang SC"',
          '"Microsoft YaHei"',
          'Roboto',
          'sans-serif',
        ],
        mono: [
          '"SF Mono"',
          'Menlo',
          'Consolas',
          '"Liberation Mono"',
          'monospace',
        ],
      },
      fontSize: {
        '2xs': ['11px', '16px'],
        xs: ['12px', '17px'],
        sm: ['13px', '19px'],
        base: ['14px', '21px'],
        md: ['16px', '23px'],
        lg: ['20px', '28px'],
        xl: ['28px', '34px'],
        '2xl': ['34px', '40px'],
      },
      spacing: {
        px: '1px',
      },
      boxShadow: {
        // 换为澄清工具同款柔投影（浅底专业风）
        panel: '0 1px 2px rgba(31,42,55,.06), 0 4px 14px rgba(31,42,55,.05)',
      },
    },
  },
  plugins: [],
};

export default config;
