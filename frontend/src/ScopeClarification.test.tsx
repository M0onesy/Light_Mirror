// @vitest-environment jsdom
import React from 'react'
import {afterEach,expect,it,vi} from 'vitest'
import {cleanup,fireEvent,render} from '@testing-library/react'
import {ResultsView,FinancialCoverageSummary} from './ResultsView'
import type {Results,CoverageStatistics} from './types'

vi.mock('../vendor/harness/packages/client/ui-primitives/src/Button',()=>({Button:({children,variant,size,...props}:any)=><button {...props}>{children}</button>}))
vi.mock('../vendor/harness/packages/client/ui-primitives/src/Tag',()=>({Tag:({children}:any)=><span>{children}</span>}))
vi.mock('../vendor/harness/packages/client/ui-primitives/src/markdown/MarkdownText',()=>({MarkdownText:({text}:any)=><div>{text}</div>}))
afterEach(cleanup)

it('submits only the explicitly selected group, keeping other gaps visible',()=>{
  const select=vi.fn(),clarify=vi.fn(),global=vi.fn()
  const group=(id:string,label:string,claim:string)=>({id,label,account:'REVENUE',table_id:null,periods:['2023Y'],claim_ids:[claim],selector:{claim_ids:[claim]},occurrences:1,blockers:[{code:'X1',kind:'fact',reason:'还缺来源材料'}]})
  const data={run_id:'local-sample',mode:'kernel',summary:{verified:0,violated:0,abstained:2},claims:[],facts:[],results:[{claim_id:'c1',status:'abstained',abstention_code:'X3',evidence:[],basis:[]},{claim_id:'c2',status:'abstained',abstention_code:'X3',evidence:[],basis:[]}],scope_clarification_groups:[group('g1','正文收入','c1'),group('g2','附表收入','c2')]} as Results
  const view=render(<ResultsView data={data} onSelect={select} onClarifyScope={global} onClarifyGroups={clarify}/>)
  const submit=view.getByRole('button',{name:'归档选定组并新建条件核查'}) as HTMLButtonElement
  expect(submit.disabled).toBe(true)
  fireEvent.change(view.getByLabelText('补充口径 正文收入'),{target:{value:'合并'}})
  expect(submit.disabled).toBe(false)
  fireEvent.click(submit)
  expect(clarify).toHaveBeenCalledWith([{scope:'合并',selector:{claim_ids:['c1']}}])
  expect(global).not.toHaveBeenCalled()
  expect(view.getAllByText('X1 · 还缺来源材料')).toHaveLength(2)
  expect(view.getByLabelText('补充口径 附表收入')).toHaveProperty('value','')
})

it('does not present conditional conclusions as source-supported coverage',()=>{
  const core={label:'公司历史财务',occurrences:2,unique_assertions:2,financial_candidates:2,counts:{verified:2,violated:0,abstained:0,pending:0},processed:2,judged:2,pending:0,processing_completion_rate:1,judgement_coverage:1,abstention_rate:0,conditional_counts:{verified:1},conditional_judged:1,source_supported_judged:1,source_supported_judgement_coverage:0.5}
  const statistics={schema_version:'financial-coverage.v3',primary_scope:'issuer_historical_financial',core,groups:{issuer_historical_financial:core},occurrences:2,verified_fact_dimensions:2,policy:'保留原文位置',claim_scopes:{},abstention_target:9,abstention_target_met:true,unconditional_unresolved:1,unconditional_abstention_target_met:true,professional_acceptance:'unknown_requires_independent_gold'} as CoverageStatistics
  const view=render(<FinancialCoverageSummary statistics={statistics}/>)
  expect(view.getByText('50.0%')).toBeTruthy()
  expect(view.getByText(/原文证据支持的判定覆盖率/)).toBeTruthy()
  expect(view.getByText(/条件核查 1 处/)).toBeTruthy()
  expect(view.getByText(/条件核查不代表原文口径已获证实/)).toBeTruthy()
})
