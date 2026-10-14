/**
 * 明镜 MingJing 弃权率归因诊断看板 —— 数据契约类型定义。
 *
 * 对应 JSON Schema `abstention-diagnosis.v1`（架构文档 §3.1 / §3.3）。
 * 本文件是看板的**唯一事实文档**：所有组件、hook、视图均以这些类型为准。
 * 数据单一来源为 `src/data/diagnosis.sample.json`，组件内禁止硬编码 PRD 数字。
 */

/** 六个弃权归因码。 */
export type XCode = 'X1' | 'X2' | 'X3' | 'X4' | 'X5' | 'X6';

/** 七类根因类别（GAP_KINDS）。 */
export type GapKind =
  | 'missing_report'
  | 'missing_detail'
  | 'definition'
  | 'external'
  | 'mention'
  | 'premise'
  | 'conflict';

/**
 * 可改善性枚举 —— 驱动 UI 颜色语义（核心叙事约定）。
 * - amendable：完全可改善（高亮色）
 * - amendable_partial：部分可改善（高亮色，弱一档）
 * - needs_user_declaration：需用户显式声明（紫色）
 * - intentional：设计上有意保守（中性冷灰 —— 是被尊重的，绝不用红）
 */
export type Amendability =
  | 'amendable'
  | 'amendable_partial'
  | 'needs_user_declaration'
  | 'intentional';

/** 需求优先级。 */
export type Priority = 'P0' | 'P1' | 'P2';

/** 造假风险等级（模拟器加权依据）。 */
export type FakeRisk = '无' | '低' | '中' | '高';

/** EVR 分母口径（PRD Q2 待确认）。 */
export type DenominatorScope = 'all_candidates' | 'main_scope';

/** 元信息。 */
export interface DiagnosisMeta {
  /** 数据基线 git HEAD。 */
  report_hash: string;
  /** 研报页数。 */
  report_pages: number;
  /** 生成时间（ISO 8601 UTC）。 */
  generated_at: string;
  /** 当前主版本 id。 */
  primary_version: string;
  /** 迁移对比版本 id。 */
  compare_version: string;
  /** EVR 分母口径：all_candidates(319) | main_scope(295)。 */
  denominator_scope: DenominatorScope;
}

/** 版本快照（每个稳定版本一条）。 */
export interface VersionSnapshot {
  id: string;
  label: string;
  total_candidates: number;
  verified: number;
  violated: number;
  abstained: number;
  /** 六码分布；X3+X2+X6 恒等于 abstained。 */
  x_codes: Record<XCode, number>;
  note?: string;
}

/** X 码字典条目。 */
export interface XCodeDef {
  code: XCode;
  name: string;
  kind: GapKind;
  amendability: Amendability;
  is_redline: boolean;
  desc: string;
}

/** GAP_KIND 字典条目（含与 X 码映射）。 */
export interface GapKindDef {
  kind: GapKind;
  name: string;
  x_code: XCode | null;
  desc: string;
}

/** 弃权构成分解条目。 */
export interface CompositionEntry {
  id: string;
  cause: string;
  count: number;
  x_code: XCode;
  gap_kind: GapKind;
  amendability: Amendability;
  evidence_ref: string;
  note: string;
}

/** 根因树节点（递归）。 */
export interface RootCauseNode {
  id: string;
  label: string;
  count: number;
  x_code?: XCode;
  /** 仅根节点：聚合口径说明（leaf 为子集量化，非穷举）。 */
  aggregation_note?: string;
  /** 是否可行动叶子。 */
  leaf?: boolean;
  actionable?: boolean;
  redline?: boolean;
  requirement_ref?: string | null;
  evidence_ref?: string;
  /** 分支口径标注：说明该节点为聚合计数（子集叶子不对齐父级）。 */
  note?: string;
  children?: RootCauseNode[];
}

/** 逐条归因条目。 */
export interface DiagnosisItem {
  id: string;
  page: number;
  seq: number;
  raw_label: string;
  x_code: XCode;
  gap_kind: GapKind;
  cause: string;
  amendability: Amendability;
  evidence_ref: string;
  requirement_ref: string | null;
}

/** 改进需求项。 */
export interface Requirement {
  id: string;
  priority: Priority;
  title: string;
  /** 救回弃权条数（模拟器输入）。 */
  rescue_estimate: number;
  cost: string;
  fake_risk: FakeRisk;
  is_redline: boolean;
  /** 关联构成分解 id（用于模拟器去重）。 */
  linked_causes: string[];
  /** 关联根因叶子 id。 */
  linked_root_leaves: string[];
  redline_constraint: string | null;
}

/** 不做清单（红区）条目。 */
export interface Redline {
  id: string;
  forbidden: string;
  reason: string;
  evidence_ref: string;
  count: number;
}

/** 北极星阶段目标刻度。 */
export interface MetricStage {
  label: string;
  value: number;
}

/** 北极星指标。 */
export interface NorthStarMetric {
  name: string;
  formula: string;
  current_value: number;
  unit: string;
  stages: MetricStage[];
  guardrail: string;
}

/** 驱动指标。 */
export interface DriverMetric {
  name: string;
  current_value: number;
  target: number | null;
  leverage: string;
}

/** 结果指标（含 unknown 状态）。 */
export interface OutcomeMetric {
  name: string;
  current_value: number | null;
  target: number | null;
  status?: string;
}

/** 反指标（必须 = 0）。 */
export interface CounterMetric {
  name: string;
  value: number;
  threshold: number;
  must_be_zero: boolean;
}

/** 指标体系。 */
export interface Metrics {
  north_star: NorthStarMetric;
  drivers: DriverMetric[];
  outcomes: OutcomeMetric[];
  counter_metrics: CounterMetric[];
}

/** 看板根数据对象。 */
export interface DiagnosisData {
  meta: DiagnosisMeta;
  versions: VersionSnapshot[];
  x_code_defs: XCodeDef[];
  gap_kind_defs: GapKindDef[];
  composition: CompositionEntry[];
  root_cause_tree: RootCauseNode;
  items: DiagnosisItem[];
  requirements: Requirement[];
  redlines: Redline[];
  metrics: Metrics;
}

/** 五个主视图 key。 */
export type ViewKey =
  | 'overview'
  | 'composition'
  | 'rootcause'
  | 'drilldown'
  | 'simulator';

/** UI 选择态（最小化）。 */
export interface UIState {
  view: ViewKey;
  activeXCode: XCode | null;
  activeRequirement: string | null;
}

/** 模拟器派生结果。 */
export interface SimResult {
  /** 救回条数（按 linked_causes 去重后）。 */
  rescued: number;
  /** 模拟后 EVR。 */
  evr: number;
  /** 造假风险加权值。 */
  riskWeighted: number;
  /** 是否勾选了触及红线的需求。 */
  redlineTouched: boolean;
}

/** 模拟器输入参数。 */
export interface SimulateParams {
  selected: string[];
  requirements: Requirement[];
  composition: CompositionEntry[];
  baseSnapshot: VersionSnapshot;
  /** EVR 分母（决定 total_candidates 的取法）。 */
  denominator: number;
}
