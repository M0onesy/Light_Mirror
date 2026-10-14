import { useEffect } from 'react';
import rawData from '@/data/diagnosis.sample.json';
import type { DiagnosisData, ViewKey } from '@/types/diagnosis';
import {
  DiagnosisProvider,
  useDiagnosisContext,
} from '@/context/DiagnosisContext';
import { validateDiagnosis } from '@/hooks/useDiagnosis';
import Layout from '@/components/Layout';
import OverviewView from '@/views/OverviewView';
import CompositionView from '@/views/CompositionView';
import RootCauseView from '@/views/RootCauseView';
import ItemDrilldownView from '@/views/ItemDrilldownView';
import SimulatorView from '@/views/SimulatorView';

/**
 * 根组件 —— Provider 包裹 + 视图切换外壳。
 *
 * 五视图：结论总览 / 构成分解 / 根因树 / 逐条下钻 / 需求模拟器。
 */

/** 强类型化内置数据（JSON 导入后断言为 DiagnosisData）。 */
const diagnosisData = rawData as unknown as DiagnosisData;

/** 内部组件：读取上下文并做视图路由。 */
function AppShell() {
  const { data, uiState, dispatch } = useDiagnosisContext();
  const { view } = uiState;

  // dev 模式：启动时校验数据契约（关键计数自洽）
  useEffect(() => {
    if (import.meta.env.DEV) {
      const errors = validateDiagnosis(data);
      if (errors.length > 0) {
        // eslint-disable-next-line no-console
        console.error('[diagnosis] 数据契约校验失败：\n' + errors.join('\n'));
      } else {
        // eslint-disable-next-line no-console
        console.info('[diagnosis] 数据契约校验通过 ✓');
      }
    }
  }, [data]);

  const handleViewChange = (v: ViewKey) => {
    dispatch({ type: 'SET_VIEW', view: v });
  };

  const renderView = () => {
    switch (view) {
      case 'overview':
        return <OverviewView onNavigate={handleViewChange} />;
      case 'composition':
        return (
          <CompositionView onDrillIntoXCode={() => handleViewChange('drilldown')} />
        );
      case 'rootcause':
        return <RootCauseView onGoToSimulator={() => handleViewChange('simulator')} />;
      case 'drilldown':
        return (
          <ItemDrilldownView onGoToSimulator={() => handleViewChange('simulator')} />
        );
      case 'simulator':
        return <SimulatorView />;
      default:
        return <OverviewView onNavigate={handleViewChange} />;
    }
  };

  return (
    <Layout view={view} onViewChange={handleViewChange} meta={data.meta}>
      {renderView()}
    </Layout>
  );
}

/** 应用根组件。 */
export default function App() {
  return (
    <DiagnosisProvider data={diagnosisData}>
      <AppShell />
    </DiagnosisProvider>
  );
}
