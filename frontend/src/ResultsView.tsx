import {useEffect,useMemo,useRef,useState} from 'react'
import {Button} from '../vendor/harness/packages/client/ui-primitives/src/Button'
import {Tag} from '../vendor/harness/packages/client/ui-primitives/src/Tag'
import {MarkdownText} from '../vendor/harness/packages/client/ui-primitives/src/markdown/MarkdownText'
import type {Results,Verdict,AnalysisMetric,Json,CoverageStatistics,ScopeClarification,ScopeClarificationGroup} from './types'
import {copy,label,markdownLabels} from './i18n'

function visibleVerdicts(data:Results):Verdict[] {
  const pending=new Map(data.coverage?.mentions.filter(row=>row.status==='pending'&&row.claim_id).map(row=>[row.claim_id!,row])||[])
  const rows=data.results.map(row=>pending.has(row.claim_id)||row.display_status==='pending'?{...row,display_status:'pending',execution_status:'pending'}:row)
  const existing=new Set(rows.map(row=>row.claim_id))
  for(const item of data.pending_items||[]){if(!existing.has(item.claim_id)){rows.push({claim_id:item.claim_id,status:'pending',display_status:'pending',execution_status:'pending',claim_text:item.claim_text||item.text,source_span:item.source_span,reason:item.reason,evidence:[],basis:[]});existing.add(item.claim_id)}}
  for(const claim of data.claims){if(pending.has(claim.claim_id)&&!existing.has(claim.claim_id))rows.push({claim_id:claim.claim_id,status:'pending',display_status:'pending',execution_status:'pending',claim_text:claim.text,source_span:{start:claim.start,end:claim.end},reason:'尚未完成原生绑定或金融工具核查，任务恢复后继续处理。',evidence:[],basis:[]})}
  return rows
}

export function ResultsView({data,onSelect,selected,onClarifyScope,onClarifyGroups}:{data:Results;onSelect:(result:Verdict)=>void;selected?:string;onClarifyScope?:(scope:string)=>void;onClarifyGroups?:(specs:ScopeClarification[])=>void}) {
  const [tab,setTab]=useState('task'),[filter,setFilter]=useState('all'),[query,setQuery]=useState(''),[page,setPage]=useState(0)
  const [scope,setScope]=useState('')
  const [scopeFilter,setScopeFilter]=useState('issuer_historical_financial')
  const [analysisPeriod,setAnalysisPeriod]=useState('latest')
  useEffect(()=>{setTab(data.task_type==='analysis'?'analysis':'task');setAnalysisPeriod('latest');setPage(0);setFilter('all');setQuery('')},[data.run_id])
  const analysisMetrics=(data.analysis?.metrics||[]) as unknown as AnalysisMetric[]
  const analysisPeriods=[...new Set(analysisMetrics.map(metric=>metric.period))]
  const defaultPeriod=(data.period&&analysisPeriods.includes(data.period)?data.period:analysisMetrics.filter(metric=>metric.status==='verified').at(-1)?.period)||analysisPeriods.at(-1)||''
  const shownPeriod=analysisPeriod==='latest'?defaultPeriod:analysisPeriod
  const shownMetrics=analysisPeriod==='all'?analysisMetrics:analysisMetrics.filter(metric=>metric.period===shownPeriod)
  const allRows=useMemo(()=>visibleVerdicts(data),[data])
  const rows=useMemo(()=>allRows.filter(row=>(!data.coverage_statistics||scopeFilter==='all'||(data.coverage_statistics.claim_scopes[row.claim_id]||'issuer_historical_financial')===scopeFilter)&&(filter==='all'||(row.display_status||row.status)===filter)&&(row.claim_text||data.claims.find(c=>c.claim_id===row.claim_id)?.text||'').includes(query)),[data,allRows,filter,query,scopeFilter])
  const summary=data.coverage_statistics?.core.counts||data.execution?.summary||Object.fromEntries(['verified','violated','abstained'].map(status=>[status,allRows.filter(row=>(row.display_status||row.status)===status).length]))
  return <><div className="workspace-tabs" role="tablist">{['task','source','coverage'].map(key=><Button key={key} variant={tab===key?'primary':'ghost'} onClick={()=>setTab(key)}>{copy[key as 'task'|'source'|'coverage']}</Button>)}{data.analysis&&<Button onClick={()=>setTab('analysis')}>{copy.analysis}</Button>}{!!data.reparse_supplements?.length&&<Button variant={tab==='supplements'?'primary':'ghost'} onClick={()=>setTab('supplements')}>补证材料</Button>}</div>
    <div className="workspace-body">
    {data.knowledge?.enabled&&<details className="knowledge-summary"><summary>会计知识库 · {data.knowledge.documents} 份材料 · {data.knowledge.blocks} 个文本块</summary><p>材料及解析缓存已冻结，Agent 可通过只读工具检索原文。外部材料的来源、版本和有效日期声明尚未独立核验，不直接授权公式或判定。</p><p className="evidence-id">知识锁：{data.knowledge.knowledge_lock}</p></details>}
    {data.scope_declaration&&<div className="notice">本任务使用用户补充声明的{data.scope_declaration}口径；声明已归档并锁定，不能据此声称原文明示了该口径。</div>}
    {!!data.scope_clarifications?.length&&<div className="notice">本任务部分陈述使用用户补充的口径。对应结果为条件核查，不能替代原文口径证据；来源表转述继续按其原始来源独立核查。</div>}
    {onClarifyGroups&&!!data.scope_clarification_groups?.length?<ScopeClarificationGroups groups={data.scope_clarification_groups!} onClarifyGroups={onClarifyGroups}/>:onClarifyScope&&data.results.some(row=>row.abstention_code==='X3'&&row.display_status!=='pending')&&<details className="scope-clarification"><summary>补充研报口径并建立新核查任务</summary><p>仅在你能明确研报使用的口径时补充。与原文明示口径冲突时拒绝覆盖。</p><select aria-label="补充研报口径" value={scope} onChange={e=>setScope(e.target.value)}><option value="">请选择明确声明的口径</option><option value="合并">我明确声明使用合并口径</option><option value="母公司">我明确声明使用母公司口径</option></select><Button disabled={!scope} onClick={()=>onClarifyScope(scope)}>归档声明并重新核查</Button></details>}
    {data.model?.status==='fallback'&&<div role="status" className="notice">Agent 未完成，已降级到确定性内核。{String(data.model.error||'模型或运行时不可用。')}</div>}
    {data.model?.status==='partial'&&<div role="status" className="notice">原生 Agent 任务部分完成。{String(data.model.error||'还有未处理的材料提及。')}</div>}
    {data.mode==='harness'&&data.task_type!=='analysis'&&summary.verified===0&&summary.violated===0&&(summary.abstained||0)>0&&<div className="notice">本任务尚未获得可核实的数字结论。请查看各条弃权原因，补充口径、可解析原文或缺失财报；处理完账本不代表研报质量达标。</div>}
    {data.mode==='harness'&&<details className="model-usage"><summary>模型与调用用量</summary><p>模式：{String(data.model?.mode||data.mode)} · 状态：{label(String(data.model?.status||'unknown'))} · 请求：{String(data.model?.request_count??'unknown')} · Token：{String(data.model?.tokens??'unknown')} · 成本：{String(data.model?.cost??'unknown')}</p>{data.model?.source_candidates!==undefined&&<p>原文候选：{String(data.model.source_candidates)} · 双盲绑定一致：{String(data.model.mapped_claims??0)}。候选数量不等于模型识别数量或金融提及召回率。</p>}</details>}
    {tab==='task'&&<>{data.coverage_statistics&&<FinancialCoverageSummary statistics={data.coverage_statistics}/>}<div className="summary">{['verified','violated','abstained'].map(status=><div key={status}><b>{summary[status]||0}</b><span>{label(status)}</span></div>)}{data.coverage&&<div><b>{data.coverage_statistics?.core.pending??data.execution?.summary.pending??allRows.filter(row=>row.display_status==='pending').length}</b><span>待处理陈述</span></div>}</div>
    {data.coverage_statistics&&<label>陈述范围<select aria-label="陈述范围" value={scopeFilter} onChange={event=>{setScopeFilter(event.target.value);setPage(0)}}><option value="issuer_historical_financial">公司历史财务（主范围）</option>{Object.entries(data.coverage_statistics.groups).filter(([key])=>key!=='issuer_historical_financial'&&key!=='rule_excluded').map(([key,group])=><option key={key} value={key}>{group.label}</option>)}<option value="all">全部判定（包含辅助范围）</option></select></label>}
    {(data.execution?.unbound_pending_mentions||0)>0&&<p className="muted">另外还有 {data.execution?.unbound_pending_mentions} 项原文提及尚未形成可执行陈述。全文账本共 {data.execution?.pending_mentions??data.coverage?.counts.pending??'未知'} 项待处理提及。</p>}
    {data.partial&&<div className="notice">本任务部分完成。已核实和有误的结论仅覆盖列出的陈述，不能据此判断整份研报质量达标。</div>}
    <div className="filter-row"><input aria-label="搜索核查陈述" placeholder="搜索原文陈述" value={query} onChange={e=>{setQuery(e.target.value);setPage(0)}}/><select aria-label="判定状态" value={filter} onChange={e=>{setFilter(e.target.value);setPage(0)}}>{['all','violated','verified','abstained','pending'].map(status=><option key={status} value={status}>{status==='all'?'全部状态':label(status)}</option>)}</select></div>
    {rows.slice(page*40,(page+1)*40).map(row=>{const status=row.display_status||row.status;const conditional=row.source_dimensions?.scope_provenance==='user_supplied_scope';return <button key={row.claim_id} className={'verdict-row '+(selected===row.claim_id?'selected':'')} onClick={()=>onSelect(row)}><div><Tag tone={status==='verified'?'success':status==='violated'?'danger':status==='pending'?'neutral':'warning'}>{label(status)}</Tag>{conditional&&<Tag tone="warning">条件核查</Tag>}{status!=='pending'&&<span className="muted">{row.error_type} {row.abstention_code}</span>}</div><p>{row.claim_text||data.claims.find(c=>c.claim_id===row.claim_id)?.text||row.claim_id}</p><small>{status==='pending'?'尚未完成执行，不能算作合法弃权。':row.reason}</small></button>})}
    <div className="pagination"><Button disabled={!page} onClick={()=>setPage(page-1)}>上一页</Button><span>{page+1} / {Math.max(1,Math.ceil(rows.length/40))} · {rows.length} 条</span><Button disabled={(page+1)*40>=rows.length} onClick={()=>setPage(page+1)}>下一页</Button></div></>}
    {tab==='source'&&<SourceText data={data} onSelect={onSelect}/>}
    {tab==='coverage'&&<CoverageView data={data} onSelect={onSelect}/>}
    {tab==='supplements'&&<>
      <h2>已应用的局部补证事实</h2><p className="muted">这些事实从原 PDF 页面重新解析，并在本任务开始前冻结。每条都可查看原页；是否支持某条研报陈述，以该陈述的独立判定为准。</p>
      {data.reparse_supplements?.map(packet=><details key={packet.packet_id} open><summary>PDF 第 {packet.pages.join('、')} 页 · 新增 {packet.added_fact_ids.length} 条事实</summary>
        {packet.added_fact_ids.map(id=>{const fact=data.reparse_fact_views?.find(item=>item.fact_id===id)||data.facts.find(item=>item.fact_id===id);if(!fact)return null;const evidence=Array.isArray(fact.evidence)?fact.evidence.filter((item):item is string=>typeof item==='string'):[];const title=String(fact.account_title||metricNames[String(fact.account)]||fact.account);const value=typeof fact.display_value==='string'?fact.display_value:`${fact.value}（标准单位未知；原披露单位 ${fact.display_unit||'未知'}）`;return <button key={id} className="verdict-row" onClick={()=>onSelect({claim_id:'supplement-'+id,status:'source_fact',claim_text:`${fact.company} ${fact.period} ${title}（${fact.scope}，${fact.nr}）`,display_value:value,evidence,basis:['局部原页确定性重解析；单位、期间和口径分别有来源证据'],operands:[fact],reason:'原页补证事实；没有在此面板生成独立研报判定。'})}>
          <p>{String(fact.period)} · {title} · {String(fact.scope)} · {String(fact.nr)}</p><b>{value}</b><small>披露日期 {String(fact.publish_date)} · 查看原始证据</small></button>})}
      </details>)}
    </>}
    {tab==='analysis'&&<>
      <h2>{copy.analysis}</h2><p className="muted">点击指标查看操作数、公式与原始财报证据。数值按来源单位与精度展示；缺失指标保留弃权原因。</p>
      <p className="analysis-summary">全部期间：可用指标 {analysisMetrics.filter(metric=>metric.status==='verified').length} 项 · 弃权 {analysisMetrics.filter(metric=>metric.status==='abstained').length} 项。此计数仅用于财报分析。</p>
      <label>显示期间<select aria-label="分析期间" value={analysisPeriod} onChange={event=>setAnalysisPeriod(event.target.value)}><option value="latest">{data.period===defaultPeriod?'任务期间':'最新可用期间'}（{defaultPeriod||'暂无'}）</option>{analysisPeriods.map(period=><option key={period} value={period}>{period}</option>)}<option value="all">全部期间</option></select></label>
      {shownMetrics.map((metric,index)=>{
        const value=metric.value===null?undefined:metric.display_value||`${metric.value} ${metric.unit||metricUnit(metric.metric)}`
        return <button key={index} className="metric-card verdict-row" onClick={()=>onSelect({...metric,claim_id:`analysis-${metric.period}-${metric.metric}`,status:metric.status,claim_text:`${metric.company||''} ${metric.period} ${metricNames[metric.metric]||metric.metric}（${metric.scope||'未知口径'}）`,expected:metric.value??undefined,display_value:value,formula:metric.formula,evidence:metric.evidence||[],operands:metric.operands||[],basis:[metric.rule_id||'登记的财报分析指标与冻结事实'],reason:metric.reason,abstention_code:metric.abstention_code})}>
          <h3>{metric.period} · {metricNames[metric.metric]||metric.metric}</h3><Tag tone={metric.status==='verified'?'success':'warning'}>{label(metric.status)}</Tag>
          <MarkdownText text={value||metric.reason||'材料不足'} labels={markdownLabels}/>{metric.display_precision_note&&<p className="muted">{metric.display_precision_note}</p>}<p className="muted">{metric.formula}</p>
        </button>
      })}
      <details><summary>分析信号与会计变更事件</summary>{['signals','events'].flatMap(category=>((category==='signals'?data.analysis?.signals:data.analysis?.events)||[]).map((record,index)=>
        <article key={`${category}-${index}`} className="form-card"><MarkdownText text={typeof record.display_text==='string'?record.display_text:'此历史记录没有中文展示文本，请查看原冻结报告。'} labels={markdownLabels}/>{Array.isArray(record.evidence)&&record.evidence.map(id=>typeof id==='string'&&<Button key={id} size="sm" onClick={()=>onSelect({claim_id:`analysis-${category}-${index}`,status:typeof record.status==='string'?record.status:'advisory',claim_text:typeof record.display_text==='string'?record.display_text:String(record.kind||'财报复核事件'),evidence:[id],basis:typeof record.rule_id==='string'?[record.rule_id]:['登记财报分析复核提示与原披露材料'],operands:Array.isArray(record.operands)?record.operands as Record<string,Json>[]:[]})}>查看原始证据</Button>)}</article>
      ))}{!data.analysis?.signals?.length&&!data.analysis?.events?.length&&<p className="muted">当前冻结材料未触发登记的分析信号或披露变更事件。</p>}</details>
    </>}
    </div></>
}

type ScopeStatusFilter='all'|'pending'|'declared'

/**
 * 口径澄清分组面板。
 *
 * 在“归档选定组并新建条件核查”之外，提供与默认首页一致的检索、状态筛选、
 * 一键折叠、逐个定位待声明分组的能力。默认全部展开，用户可以逐组声明口径；
 * 声明只会新建一次条件核查，不会把已有结果改成已核实，也不会默认采用合并口径。
 */
export function ScopeClarificationGroups({groups,onClarifyGroups}:{groups:ScopeClarificationGroup[];onClarifyGroups:(specs:ScopeClarification[])=>void}) {
  const [groupScopes,setGroupScopes]=useState<Record<string,string>>({})
  const [query,setQuery]=useState('')
  const [statusFilter,setStatusFilter]=useState<ScopeStatusFilter>('all')
  const [collapsed,setCollapsed]=useState<Record<string,boolean>>({})
  const [flash,setFlash]=useState('')
  const listRef=useRef<HTMLDivElement>(null)
  const keyword=query.trim().toLowerCase()
  const declaredCount=groups.filter(group=>groupScopes[group.id]).length
  const pendingCount=groups.length-declaredCount
  const shown=groups.filter(group=>(!keyword||[group.label,group.account,group.table_id||''].join(' ').toLowerCase().includes(keyword))&&(statusFilter==='all'||(statusFilter==='declared'?Boolean(groupScopes[group.id]):!groupScopes[group.id])))
  const allCollapsed=shown.length>0&&shown.every(group=>Boolean(collapsed[group.id]))
  const toggleGroup=(id:string)=>setCollapsed(value=>({...value,[id]:!value[id]}))
  const toggleAll=()=>{const next=!allCollapsed;setCollapsed(Object.fromEntries(shown.map(group=>[group.id,next])))}
  const locateNext=()=>{
    const target=groups.find(group=>!groupScopes[group.id]&&shown.some(item=>item.id===group.id))||groups.find(group=>!groupScopes[group.id])
    if(!target)return
    if(!shown.some(item=>item.id===target.id)){setQuery('');setStatusFilter('all')}
    setCollapsed(value=>({...value,[target.id]:false}))
    setFlash(target.id)
    window.setTimeout(()=>setFlash(current=>current===target.id?'':current),1400)
    window.requestAnimationFrame(()=>{const element=listRef.current?.querySelector<HTMLElement>(`#scope-group-${target.id}`);element?.scrollIntoView?.({block:'center',behavior:'smooth'})})
  }
  const submitSpecs=()=>groups.filter(group=>groupScopes[group.id]).map(group=>({scope:groupScopes[group.id],selector:group.selector}))
  return <details className="scope-clarification" open>
    <summary>按表或指标补充口径 · {groups.reduce((total,group)=>total+group.occurrences,0)} 个原文位置</summary>
    <div className="scope-banner"><b>口径待澄清</b><p>下列条目因原文没有声明会计口径而未能核实，并非数字有误。声明口径会新建一次条件核查，<b>不会把已有结果改成已核实</b>；系统<b>不会默认采用合并口径</b>，声明须由本人确认并写入审计留痕。</p></div>
    <p>请先补充来源说明；若你已明确使用口径，可只声明下面选定的组。其他阻断原因仍保留，声明不保证能解除缺方法或缺材料。</p>
    <div className="scope-tools">
      <input aria-label="检索口径分组" placeholder="检索科目、口径、来源表…" value={query} onChange={event=>setQuery(event.target.value)}/>
      <div className="scope-chips" role="group" aria-label="按声明状态筛选">
        {([['all','全部'],['pending','待声明'],['declared','已声明']] as [ScopeStatusFilter,string][]).map(([value,text])=><button key={value} type="button" className={'scope-chip'+(statusFilter===value?' active':'')} aria-pressed={statusFilter===value} onClick={()=>setStatusFilter(value)}>{text}</button>)}
      </div>
      <button type="button" className="scope-tool-btn" onClick={toggleAll}>{allCollapsed?'展开全部':'收起全部'}</button>
      <button type="button" className="scope-tool-btn" onClick={locateNext}>下一个待声明</button>
    </div>
    <p className="scope-tally">共 <b>{groups.length}</b> 组口径待澄清 · 已声明 <b>{declaredCount}</b> · 待声明 <b>{pendingCount}</b>{shown.length===groups.length?'':` · 当前筛选显示 ${shown.length} 组`}</p>
    <div className="scope-group-list" ref={listRef}>
      {shown.length===0?<p className="scope-empty muted">没有匹配当前检索或筛选条件的口径分组。请调整关键词或切换「全部／待声明／已声明」状态筛选。</p>:shown.map(group=>{const isCollapsed=Boolean(collapsed[group.id]);return <div key={group.id} id={`scope-group-${group.id}`} className={'scope-card'+(groupScopes[group.id]?' declared':'')+(flash===group.id?' flash':'')}>
        <div className="scope-card-head">
          <button type="button" className="scope-toggle" aria-label={`展开或收起 ${group.label}`} aria-expanded={!isCollapsed} onClick={()=>toggleGroup(group.id)}>{isCollapsed?'▸':'▾'}</button>
          <b>{group.label}</b>
          <span className="scope-card-meta muted">{group.account}{group.table_id?` · ${group.table_id}`:''}</span>
          <span className="scope-card-periods muted">{group.periods.join('、')} · {group.occurrences} 处</span>
        </div>
        <div className="scope-card-body" hidden={isCollapsed}>
          <label className="scope-card-declare">{group.label} · {group.periods.join('、')} · {group.occurrences} 处<select aria-label={`补充口径 ${group.label}`} value={groupScopes[group.id]||''} onChange={event=>setGroupScopes(value=>({...value,[group.id]:event.target.value}))}><option value="">不补充声明</option><option value="合并">我明确声明该组为合并口径</option><option value="母公司">我明确声明该组为母公司口径</option></select></label>
          <p className="scope-cids muted">{group.claim_ids.join('、')}</p>
          {group.blockers.map((blocker,index)=><p key={index} className="scope-blocker">{blocker.code} · {blocker.reason}</p>)}
        </div>
      </div>})}
    </div>
    <Button disabled={!groups.some(group=>groupScopes[group.id])} onClick={()=>onClarifyGroups(submitSpecs())}>归档选定组并新建条件核查</Button>
  </details>
}
const metricNames:Record<string,string>={REVENUE:'营业收入',COST:'营业成本',NP:'净利润',NP_PARENT:'归母净利润',NP_PARENT_ADJ:'扣非归母净利润',CFO:'经营现金流',ASSETS:'总资产',LIABILITIES:'总负债',EQUITY_PARENT:'归母权益',EPS:'基本每股收益',EPS_DILUTED:'稀释每股收益',ROE:'加权平均净资产收益率',GROSS_MARGIN:'毛利率',NET_MARGIN:'净利率',DEBT_RATIO:'资产负债率',CASH_CONTENT:'经营现金流含金量',NP_PARENT_RECOMPUTED:'四项重算扣非归母净利润',NON_RECURRING_SHARE:'非经常性损益占比',REVENUE_YOY:'营业收入同比',NP_PARENT_YOY:'归母净利润同比',CFO_YOY:'经营现金流同比'}
function metricUnit(metric:string) {return metric.endsWith('_YOY')||['ROE','GROSS_MARGIN','NET_MARGIN','DEBT_RATIO','CASH_CONTENT','NON_RECURRING_SHARE'].includes(metric)?'比例':metric==='EPS'||metric==='EPS_DILUTED'?'元/股':'元'}

export function SourceText({data,onSelect}:{data:Results;onSelect:(result:Verdict)=>void}) {
  // Frozen Python source offsets count Unicode code points, not UTF-16 units.
  const text=data.draft_text||'', points=Array.from(text), chunks:{text:string;row?:Verdict}[]=[], sorted=visibleVerdicts(data).filter(row=>row.source_span&&Number.isInteger(row.source_span.start)&&Number.isInteger(row.source_span.end)&&row.source_span.start>=0&&row.source_span.end>row.source_span.start&&row.source_span.end<=points.length).sort((a,b)=>a.source_span!.start-b.source_span!.start)
  let cursor=0
  for(const row of sorted){const span=row.source_span!;if(span.start<cursor)continue;if(span.start>cursor)chunks.push({text:points.slice(cursor,span.start).join('')});chunks.push({text:points.slice(span.start,span.end).join(''),row});cursor=span.end}
  if(cursor<points.length)chunks.push({text:points.slice(cursor).join('')})
  return <div className="source-text">{text?chunks.map((chunk,index)=>chunk.row?<mark key={index} tabIndex={0} className={chunk.row.display_status||chunk.row.status} onClick={()=>onSelect(chunk.row!)} onKeyDown={e=>{if(e.key==='Enter')onSelect(chunk.row!)}}>{chunk.text}</mark>:<span key={index}>{chunk.text}</span>):'该任务没有冻结研报原文。'}</div>
}

function CoverageView({data,onSelect}:{data:Results;onSelect:(result:Verdict)=>void}) {
  const [filter,setFilter]=useState('pending'),[page,setPage]=useState(0),coverage=data.coverage
  if(!coverage)return <p className="muted">此任务没有 Agent 全文覆盖账本。</p>
  const rows=coverage.mentions.filter(row=>filter==='all'||row.status===filter)
  return <><h2>全文覆盖账本</h2>{data.coverage_statistics&&<FinancialCoverageSummary statistics={data.coverage_statistics}/>}<SourceGapView data={data} onSelect={onSelect}/><p>全部候选 {coverage.financial_candidates} · 已处理 {coverage.processed} · 处理比例 {coverage.coverage===null?'未知':(coverage.coverage*100).toFixed(1)+'%'}</p><p className="muted">处理比例包含已判定与弃权，不能当作金融提及召回率；召回率需要独立金标评测。</p><select aria-label="账本状态" value={filter} onChange={e=>{setFilter(e.target.value);setPage(0)}}>{['pending','abstained','judged','out_of_scope','rule_excluded','all'].map(status=><option key={status} value={status}>{status==='all'?'全部提及':status==='judged'?'已判定':label(status)}（{status==='all'?coverage.mentions.length:coverage.counts[status]||0}）</option>)}</select>
  <table><thead><tr><th>原文提及</th><th>位置</th><th>状态与原因</th></tr></thead><tbody>{rows.slice(page*60,(page+1)*60).map(row=><tr key={row.id}><td>{row.text}</td><td>第 {coverage.pages.find(p=>p.id===row.page_id)?.page||'?'} 页<br/><small>字符 {row.start}–{row.end}</small></td><td>{label(row.status)}<br/><small>{row.reason}</small></td></tr>)}</tbody></table><div className="pagination"><Button disabled={!page} onClick={()=>setPage(page-1)}>上一页</Button><span>{rows.length} 项</span><Button disabled={(page+1)*60>=rows.length} onClick={()=>setPage(page+1)}>下一页</Button></div></>
}

export function SourceGapView({data,onSelect}:{data:Results;onSelect?:(result:Verdict)=>void}) {
  const [page,setPage]=useState(0),rows=data.source_gaps||[]
  if(!rows.length)return null
  return <details><summary>来源表口径与方法登记（{rows.length} 项）</summary><p className="muted">以下记录保留尚需确认的会计口径或方法。具备明确引用与表身份的单元格可核查转述；相关结论见核查结果。未确定的口径仍不能用于跨表计算，登记本身不计作核实。</p><table><thead><tr><th>来源表／维度</th><th>需补依据</th><th>披露时序</th></tr></thead><tbody>{rows.slice(page*20,(page+1)*20).map(row=><tr key={row.id}><td>{row.table_label||row.account}<br/>{row.period} {row.dimension_id}<br/><small>{row.id}</small>{onSelect&&row.evidence.length>0&&<button className="evidence-link" onClick={()=>onSelect({claim_id:row.id,status:'source_quote',claim_text:`${row.table_label||row.account} ${row.period||''} ${row.dimension_id||''}`,reason:row.needed_method||row.reason||'\u539f\u6587\u5b9a\u4e49\u5f85\u786e\u8ba4',evidence:row.evidence,basis:[]})}>{'\u67e5\u770b\u539f\u59cbPDF\u9875'}</button>}</td><td>{row.needed_method||row.reason}<details><summary>锁定来源标识</summary><pre>{JSON.stringify({report_id:row.report_id,evidence:row.evidence},null,2)}</pre></details></td><td>{row.publish_date||'日期未知'}<br/>{row.public_at_cutoff?'截至研报日已披露':'不能作为当时已知依据'}</td></tr>)}</tbody></table><div className="pagination"><Button disabled={!page} onClick={()=>setPage(page-1)}>上一页</Button><span>{page+1} / {Math.ceil(rows.length/20)}</span><Button disabled={(page+1)*20>=rows.length} onClick={()=>setPage(page+1)}>下一页</Button></div></details>
}

export function FinancialCoverageSummary({statistics}:{statistics:CoverageStatistics}) {
  const core=statistics.core,percent=(value:number|null|undefined)=>value===null||value===undefined?'未知':`${(value*100).toFixed(1)}%`
  return <section aria-label="金融核查覆盖"><h3>公司历史财务核查</h3><p>主范围 {core.financial_candidates} 处 · 去重断言 {core.unique_assertions} 条 · 核实事实维度 {statistics.verified_fact_dimensions} 项</p><p>处理完成率 <strong>{percent(core.processing_completion_rate)}</strong> · 有效判定覆盖率 <strong>{percent(core.judgement_coverage)}</strong> · 弃权率 <strong>{percent(core.abstention_rate)}</strong> · 待处理 {core.pending} 处</p>{core.source_supported_judgement_coverage!==undefined&&<p>原文证据支持的判定覆盖率 <strong>{percent(core.source_supported_judgement_coverage)}</strong> · 条件核查 {core.conditional_judged||0} 处 · 原文未解决 {statistics.unconditional_unresolved??'未知'} 处</p>}<p className="muted">{statistics.policy} 已处理包含弃权；去重不减少验收分母。条件核查不代表原文口径已获证实。专业准确性与召回率须另做独立金标评测。</p><details><summary>查看全部范围统计与未核实内容</summary><table><thead><tr><th>范围</th><th>原文出现</th><th>核实</th><th>违背</th><th>弃权</th><th>条件判定</th><th>待处理</th></tr></thead><tbody>{Object.entries(statistics.groups).map(([key,group])=><tr key={key}><td>{group.label}</td><td>{group.occurrences}</td><td>{group.counts.verified||0}</td><td>{group.counts.violated||0}</td><td>{group.counts.abstained||0}</td><td>{group.conditional_judged||0}</td><td>{group.pending}</td></tr>)}</tbody></table><p>全文保留 {statistics.occurrences} 个出现位置。预测、假设及排除项可在全文账本查看。</p></details></section>
}
