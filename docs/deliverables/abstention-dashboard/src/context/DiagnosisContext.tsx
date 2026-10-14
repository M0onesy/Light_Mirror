import {
  createContext,
  useContext,
  useReducer,
  type Dispatch,
  type ReactNode,
} from 'react';
import type { DiagnosisData, UIState, ViewKey, XCode } from '@/types/diagnosis';

/**
 * 诊断数据上下文 —— 提供单一数据源（DiagnosisData）与最小化 UI 选择态。
 * 采用 React Context + useReducer，不引入任何状态管理库。
 */

/** UI 动作类型。 */
export type UIAction =
  | { type: 'SET_VIEW'; view: ViewKey }
  | { type: 'SET_XCODE'; code: XCode | null }
  | { type: 'SET_REQUIREMENT'; id: string | null }
  | { type: 'CLEAR_SELECTION' };

/** 初始 UI 状态：默认落在「结论总览」，无任何选中。 */
export const initialUIState: UIState = {
  view: 'overview',
  activeXCode: null,
  activeRequirement: null,
};

/** UI 归约器：只管理 view / activeXCode / activeRequirement 三项选择态。 */
export function uiReducer(state: UIState, action: UIAction): UIState {
  switch (action.type) {
    case 'SET_VIEW':
      return { ...state, view: action.view };
    case 'SET_XCODE':
      return { ...state, activeXCode: action.code };
    case 'SET_REQUIREMENT':
      return { ...state, activeRequirement: action.id };
    case 'CLEAR_SELECTION':
      return { ...state, activeXCode: null, activeRequirement: null };
    default:
      return state;
  }
}

/** Context 值结构。 */
export interface DiagnosisContextValue {
  data: DiagnosisData;
  uiState: UIState;
  dispatch: Dispatch<UIAction>;
}

const DiagnosisContext = createContext<DiagnosisContextValue | null>(null);

/** Provider Props。 */
export interface DiagnosisProviderProps {
  data: DiagnosisData;
  children: ReactNode;
}

/** 诊断数据 Provider —— 包裹整个应用。 */
export function DiagnosisProvider({ data, children }: DiagnosisProviderProps) {
  const [uiState, dispatch] = useReducer(uiReducer, initialUIState);
  return (
    <DiagnosisContext.Provider value={{ data, uiState, dispatch }}>
      {children}
    </DiagnosisContext.Provider>
  );
}

/** 读取诊断上下文；未包裹 Provider 时抛错（快速定位接入问题）。 */
export function useDiagnosisContext(): DiagnosisContextValue {
  const ctx = useContext(DiagnosisContext);
  if (ctx === null) {
    throw new Error(
      'useDiagnosisContext 必须在 <DiagnosisProvider> 内部使用。'
    );
  }
  return ctx;
}
