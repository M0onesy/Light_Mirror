// @vitest-environment jsdom
import React from 'react'
import {afterEach,expect,it,vi} from 'vitest'
import {cleanup,fireEvent,render} from '@testing-library/react'
import {SourceText,FinancialCoverageSummary,SourceGapView} from './ResultsView'
import type {Results,CoverageStatistics} from './types'

vi.mock('../vendor/harness/packages/client/ui-primitives/src/Button',()=>({Button:({children}:any)=><button>{children}</button>}))
vi.mock('../vendor/harness/packages/client/ui-primitives/src/Tag',()=>({Tag:({children}:any)=><span>{children}</span>}))
vi.mock('../vendor/harness/packages/client/ui-primitives/src/markdown/MarkdownText',()=>({MarkdownText:({text}:any)=><div>{text}</div>}))
afterEach(cleanup)

it('opens unresolved source pages as quotations without financial verdicts',()=>{
  const gap={id:'sg-original',table_label:'客户收入',period:'2022Y',dimension_id:'客户B',
    needed_method:'发行人口径待确认',publish_date:'2023-10-16',public_at_cutoff:true,
    report_id:'report-locked',evidence:['original-page'],eligible_for_verification:false}
  const select=vi.fn()
  const {getByText}=render(<SourceGapView data={{source_gaps:[gap]} as unknown as Results} onSelect={select}/>)
  fireEvent.click(getByText('查看原始PDF页'))
  expect(select).toHaveBeenCalledWith(expect.objectContaining({status:'source_quote',
    evidence:['original-page'],reason:'发行人口径待确认'}))
  expect(select.mock.calls[0][0]).not.toHaveProperty('expected')
  expect(select.mock.calls[0][0]).not.toHaveProperty('operands')
})

it('matches frozen Python codepoint addresses after supplementary characters',()=>{
  const original='😀𠮷公司：收入10亿元。尾部🚀'
  const data={draft_text:original,results:[{claim_id:'income',status:'verified',source_span:{start:5,end:11}}],claims:[]} as unknown as Results
  const select=vi.fn()
  const {container}=render(<SourceText data={data} onSelect={select}/>)
  const mark=container.querySelector('mark')!
  expect(mark.textContent).toBe('收入10亿元')
  expect(container.textContent).toBe(original)
  fireEvent.click(mark)
  expect(select).toHaveBeenCalledWith(data.results[0])
  fireEvent.keyDown(mark,{key:'Enter'})
  expect(select).toHaveBeenCalledTimes(2)
})

it('ignores malformed or UTF-16-only spans without hiding original text',()=>{
  const original='😀𠮷公司：收入10亿元。尾部🚀'
  const spans=[{start:0,end:0},{start:1,end:0},{start:-1,end:2},{start:0.5,end:2},{start:14,end:18}]
  const data={draft_text:original,results:spans.map((source_span,i)=>({claim_id:`invalid-${i}`,status:'verified',source_span})),claims:[]} as unknown as Results
  const {container}=render(<SourceText data={data} onSelect={vi.fn()}/>)
  expect(container.querySelectorAll('mark')).toHaveLength(0)
  expect(container.textContent).toBe(original)
})

it('distinguishes completed abstentions from effective financial coverage and exposes auxiliary gaps',()=>{
  const core={label:'公司历史财务',occurrences:3,unique_assertions:2,financial_candidates:3,counts:{verified:1,violated:0,abstained:2,pending:0},processed:3,judged:1,pending:0,processing_completion_rate:1,judgement_coverage:1/3,abstention_rate:2/3}
  const industry={...core,label:'行业历史事实',occurrences:1,counts:{verified:0,violated:0,abstained:1,pending:0}}
  const statistics:CoverageStatistics={schema_version:'financial-coverage.v2',primary_scope:'issuer_historical_financial',core,groups:{issuer_historical_financial:core,industry_historical:industry},occurrences:4,verified_fact_dimensions:1,policy:'主范围按原文出现位置计算',claim_scopes:{},abstention_target:9,abstention_target_met:true,professional_acceptance:'unknown_requires_independent_gold'}
  const {getByText,getByRole}=render(<FinancialCoverageSummary statistics={statistics}/>)
  expect(getByText('100.0%')).toBeTruthy()
  expect(getByText('33.3%')).toBeTruthy()
  expect(getByText('66.7%')).toBeTruthy()
  fireEvent.click(getByText('查看全部范围统计与未核实内容'))
  expect(getByRole('cell',{name:'行业历史事实'})).toBeTruthy()
  expect(getByText('全文保留 4 个出现位置。预测、假设及排除项可在全文账本查看。')).toBeTruthy()
})
