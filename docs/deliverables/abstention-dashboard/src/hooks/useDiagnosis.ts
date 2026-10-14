import { useCallback, useMemo } from 'react';
import { useDiagnosisContext, type UIAction } from '@/context/DiagnosisContext';
import type {
  Amendability,
  DiagnosisData,
  DiagnosisItem,
  DenominatorScope,
  FakeRisk,
  Requirement,
  SimResult,
  SimulateParams,
  VersionSnapshot,
  XCode,
} from '@/types/diagnosis';

/**
 * 派生指标 Hook —— 所有统计（EVR、计数、分组、模拟）集中在此，
 * 保证单一计算源，禁止在视图组件里重算。
 */

/** 造假风险加权系数（架构文档 §4.3 约定）。 */
export const RISK_FACTOR: Record<FakeRisk, number> = {
  无: 0,
  低: 0.1,
  中: 0.3,
  高: 0.6,
};

/** 六码顺序（稳定展示顺序）。 */
export const X_CODE_ORDER: XCode[] = ['X1', 'X2', 'X3', 'X4', 'X5', 'X6'];

/** 三个「恒等弃权」的主码（X3+X2+X6 === abstained）。 */
export const PRIMARY_X_CODES: XCode[] = ['X3', 'X2', 'X6'];

/** 可改善性 → 中文标签，供 UI 复用。 */
export const AMENDABILITY_LABEL: Record<Amendability, string> = {
  amendable: '可改善',
  amendable_partial: '部分可改善',
  needs_user_declaration: '待用户声明',
  intentional: '有意保守',
};

/** 按 amendability 聚合的构成段（AbstentionBar 输入）。 */
export interface AmendabilitySegment {
  amendability: Amendability;
  label: string;
  count: number;
}

/**
 * 校验诊断数据契约（架构 §3.1）。
 * 关键不变式：
 * 1. 每个版本 X3+X2+X6 === abstained。
 * 2. verified + violated + abstained === total_candidates。
 * 3. composition 计数与主版本 X 码分布量级一致。
 * @returns 错误信息数组；为空表示校验通过。
 */
export function validateDiagnosis(data: DiagnosisData): string[] {
  const errors: string[] = [];

  for (const v of data.versions) {
    const xSum =
      (v.x_codes.X1 ?? 0) +
      (v.x_codes.X2 ?? 0) +
      (v.x_codes.X3 ?? 0) +
      (v.x_codes.X4 ?? 0) +
      (v.x_codes.X5 ?? 0) +
      (v.x_codes.X6 ?? 0);
    if (xSum !== v.abstained) {
      errors.push(
        `[${v.id}] 六码合计 ${xSum} ≠ abstained ${v.abstained}`
      );
    }
    const total = v.verified + v.violated + v.abstained;
    if (total !== v.total_candidates) {
      errors.push(
        `[${v.id}] verified+violated+abstained = ${total} ≠ total_candidates ${v.total_candidates}`
      );
    }
  }

  const primary = data.meta.primary_version;
  const pv = data.versions.find((v) => v.id === primary);
  if (pv === undefined) {
    errors.push(`找不到主版本 ${primary}`);
  }

  // composition 计数合计应与「解析后剩余历史分组」量级一致（此处校验 >0）
  const compSum = data.composition.reduce((acc, c) => acc + c.count, 0);
  if (compSum <= 0) {
    errors.push('composition 计数合计为 0，数据异常');
  }

  // requirements 引用的 cause / root_leaves 必须存在
  const causeIds = new Set(data.composition.map((c) => c.id));
  for (const req of data.requirements) {
    for (const cid of req.linked_causes) {
      if (!causeIds.has(cid)) {
        errors.push(`需求 ${req.id} 引用了不存在的 cause ${cid}`);
      }
    }
  }

  return errors;
}

/** useDiagnosis 返回结构。 */
export interface UseDiagnosisResult {
  data: DiagnosisData;
  dispatch: (a: UIAction) => void;
  activeVersion: VersionSnapshot;
  compareVersion: VersionSnapshot;
  primaryVersion: VersionSnapshot;
  /** EVR 分母（由 denominator_scope 决定）。 */
  denominator: number;
  /** 北极星派生 EVR = (verified+violated)/denominator。 */
  evr: number;
  /** 可改善条数（amendable + amendable_partial）。 */
  amendableCount: number;
  /** 有意保守条数（intentional）。 */
  redlineCount: number;
  /** 待用户声明条数。 */
  declareCount: number;
  /** 构成的 amendability 分组段。 */
  compositionByAmendability: AmendabilitySegment[];
  /** 当前选中的 X 码。 */
  activeXCode: XCode | null;
  /** 按 X 码过滤后的 items。 */
  filteredItems: DiagnosisItem[];
  /** 分组后的 requirements。 */
  requirementsByPriority: Record<'P0' | 'P1' | 'P2', Requirement[]>;
  /** 模拟器纯函数。 */
  simulate: (params: SimulateParams) => SimResult;
}

/**
 * 主派生 Hook —— 从上下文读取 data 与 uiState，memo 化所有派生值。
 */
export function useDiagnosis(): UseDiagnosisResult {
  const { data, uiState, dispatch } = useDiagnosisContext();

  const byVersionId = useMemo(() => {
    const map = new Map<string, VersionSnapshot>();
    for (const v of data.versions) {
      map.set(v.id, v);
    }
    return map;
  }, [data.versions]);

  const activeVersion = useMemo<VersionSnapshot>(() => {
    const v = byVersionId.get(data.meta.primary_version);
    if (v === undefined) {
      throw new Error(`主版本 ${data.meta.primary_version} 不存在于 versions[]`);
    }
    return v;
  }, [byVersionId, data.meta.primary_version]);

  const compareVersion = useMemo<VersionSnapshot>(() => {
    const v = byVersionId.get(data.meta.compare_version);
    if (v === undefined) {
      throw new Error(`对比版本 ${data.meta.compare_version} 不存在于 versions[]`);
    }
    return v;
  }, [byVersionId, data.meta.compare_version]);

  const denominator = useMemo(() => {
    const scope: DenominatorScope = data.meta.denominator_scope;
    if (scope === 'main_scope') {
      const full = byVersionId.get('stable-16-full');
      return full !== undefined ? full.total_candidates : activeVersion.total_candidates;
    }
    return activeVersion.total_candidates;
  }, [data.meta.denominator_scope, byVersionId, activeVersion]);

  const evr = useMemo(() => {
    const numerator = activeVersion.verified + activeVersion.violated;
    return denominator > 0 ? numerator / denominator : 0;
  }, [activeVersion, denominator]);

  /** 按 amendability 聚合构成分解。 */
  const compositionByAmendability = useMemo<AmendabilitySegment[]>(() => {
    const buckets = new Map<Amendability, number>();
    for (const entry of data.composition) {
      buckets.set(
        entry.amendability,
        (buckets.get(entry.amendability) ?? 0) + entry.count
      );
    }
    const order: Amendability[] = [
      'amendable',
      'amendable_partial',
      'needs_user_declaration',
      'intentional',
    ];
    return order
      .filter((a) => (buckets.get(a) ?? 0) > 0)
      .map((a) => ({
        amendability: a,
        label: AMENDABILITY_LABEL[a],
        count: buckets.get(a) ?? 0,
      }));
  }, [data.composition]);

  const amendableCount = useMemo(
    () =>
      compositionByAmendability
        .filter(
          (s) =>
            s.amendability === 'amendable' ||
            s.amendability === 'amendable_partial'
        )
        .reduce((acc, s) => acc + s.count, 0),
    [compositionByAmendability]
  );

  const redlineCount = useMemo(
    () =>
      compositionByAmendability
        .filter((s) => s.amendability === 'intentional')
        .reduce((acc, s) => acc + s.count, 0),
    [compositionByAmendability]
  );

  const declareCount = useMemo(
    () =>
      compositionByAmendability
        .filter((s) => s.amendability === 'needs_user_declaration')
        .reduce((acc, s) => acc + s.count, 0),
    [compositionByAmendability]
  );

  const filteredItems = useMemo<DiagnosisItem[]>(() => {
    if (uiState.activeXCode === null) {
      return data.items;
    }
    return data.items.filter((i) => i.x_code === uiState.activeXCode);
  }, [data.items, uiState.activeXCode]);

  const requirementsByPriority = useMemo(() => {
    const groups: Record<'P0' | 'P1' | 'P2', Requirement[]> = {
      P0: [],
      P1: [],
      P2: [],
    };
    for (const r of data.requirements) {
      groups[r.priority].push(r);
    }
    return groups;
  }, [data.requirements]);

  const simulate = useCallback(
    (params: SimulateParams): SimResult => simulateDiagnosis(params),
    []
  );

  return {
    data,
    dispatch,
    activeVersion,
    compareVersion,
    primaryVersion: activeVersion,
    denominator,
    evr,
    amendableCount,
    redlineCount,
    declareCount,
    compositionByAmendability,
    activeXCode: uiState.activeXCode,
    filteredItems,
    requirementsByPriority,
    simulate,
  };
}

/**
 * 模拟器纯函数 —— 计算勾选需求后的救回条数与 EVR。
 *
 * 去重规则：救回条数按 `linked_causes` 归并（同一 cause 只计一次），
 * 无 linked_causes 的需求（横切型，如 P0-2 前置短路）按其自身 rescue_estimate 直接计入。
 * 造假风险加权：Σ rescue_estimate × riskFactor(fake_risk)。
 * 红线检测：勾选任一项 is_redline=true 即触发。
 */
export function simulateDiagnosis(params: SimulateParams): SimResult {
  const { selected, requirements, composition, baseSnapshot, denominator } = params;
  const selectedSet = new Set(selected);
  const chosen = requirements.filter((r) => selectedSet.has(r.id));

  // cause → count 映射，用于按 cause 去重
  const causeCount = new Map<string, number>();
  for (const c of composition) {
    causeCount.set(c.id, c.count);
  }

  const countedCauses = new Set<string>();
  let rescued = 0;
  let riskWeighted = 0;
  let redlineTouched = false;

  for (const req of chosen) {
    if (req.is_redline) {
      redlineTouched = true;
    }
    riskWeighted += req.rescue_estimate * RISK_FACTOR[req.fake_risk];

    if (req.linked_causes.length > 0) {
      // 按 cause 去重计数
      for (const cid of req.linked_causes) {
        if (countedCauses.has(cid)) {
          continue;
        }
        countedCauses.add(cid);
        rescued += causeCount.get(cid) ?? 0;
      }
      // 若 rescue_estimate 大于其 cause 计数的部分，视为横切增量
      const linkedSum = req.linked_causes.reduce(
        (acc, cid) => acc + (causeCount.get(cid) ?? 0),
        0
      );
      if (req.rescue_estimate > linkedSum) {
        rescued += req.rescue_estimate - linkedSum;
      }
    } else {
      // 横切型需求：直接计入 rescue_estimate
      rescued += req.rescue_estimate;
    }
  }

  // 救回不得超过当前弃权总数
  const maxRescue = baseSnapshot.abstained;
  const cappedRescue = Math.min(rescued, maxRescue);

  const numerator = baseSnapshot.verified + baseSnapshot.violated + cappedRescue;
  const evr = denominator > 0 ? numerator / denominator : 0;

  return {
    rescued: cappedRescue,
    evr,
    riskWeighted,
    redlineTouched,
  };
}
