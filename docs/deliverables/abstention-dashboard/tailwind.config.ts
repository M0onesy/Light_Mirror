import type { Config } from 'tailwindcss';

/**
 * Tailwind 主题配置 —— 明镜诊断看板设计 token。
 *
 * 核心叙事约定（不可违反）：
 * - 合法弃权（X2/X4、premise/conflict）用**克制的冷灰/石板色**表达，绝不用错误红。
 * - 「可改善空间」用高亮色（青/琥珀）；「待用户声明」用紫。
 * - 仅反指标 >0 或红线触碰才用红。
 */
const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // 背景（终端底）
        base: '#0B0E13',
        panel: '#141821',
        'panel-alt': '#10141C',
        border: '#1F2937',
        // 合法弃权（中性冷灰/石板色）—— 它们是被尊重的
        abstain: '#6B7280',
        'abstain-muted': '#4B5563',
        'abstain-dim': '#374151',
        // 可改善空间（高亮）
        improve: '#38BDF8',
        'improve-alt': '#F59E0B',
        // 待用户声明
        declare: '#A78BFA',
        // 警戒（仅真正违规）
        alert: '#EF4444',
        'alert-soft': '#7F1D1D',
        // 文本
        'text-primary': '#E5E7EB',
        'text-secondary': '#9CA3AF',
        'text-muted': '#6B7280',
        // 合规绿（反指标=0）
        ok: '#34D399',
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
        panel: '0 1px 0 0 rgba(255,255,255,0.02) inset, 0 8px 24px -12px rgba(0,0,0,0.6)',
      },
    },
  },
  plugins: [],
};

export default config;
