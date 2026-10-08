import {useEffect,useRef,useState,useSyncExternalStore} from 'react'
import {Button} from '../vendor/harness/packages/client/ui-primitives/src/Button'
import {Tag} from '../vendor/harness/packages/client/ui-primitives/src/Tag'
import {MarkdownText} from '../vendor/harness/packages/client/ui-primitives/src/markdown/MarkdownText'
import {DockController,DockSurface,FloatLayer} from '../vendor/harness/packages/client/ui-dockkit/src/index'
import type {TabRecord} from '../vendor/harness/packages/client/ui-dockkit/src/contract/types'
import {api,fetchBlob} from './api'
import type {Run,RunEvent,Verdict} from './types'
import {copy,dockLabels,label,markdownLabels} from './i18n'
import {ReparsePackets} from './ReparsePackets'
import type {ReparsePacket} from './ReparsePackets'

type ReparseCapability={available:boolean;report_id?:string;page?:number;reason?:string}

export function Inspector({verdict,runId,events,onClose,onNewRun,width}:{verdict?:Verdict;runId?:string;events:RunEvent[];onClose:()=>void;onNewRun?:(run:Run)=>void;width:number}) {
  const [dock]=useState(()=>new DockController({makeInitialTab:id=>({id,kind:'verdict',contentId:'selected-verdict',title:copy.verdict})}))
  const {state,canSplit}=useSyncExternalStore(dock.subscribe,dock.getSnapshot)
  const [evidence,setEvidence]=useState<string>(),[info,setInfo]=useState<Record<string,unknown>>(),[error,setError]=useState('')
  const [imageUrl,setImageUrl]=useState<string>()
  const [loadedEvidenceUrl,setLoadedEvidenceUrl]=useState<string>()
  const [packets,setPackets]=useState<ReparsePacket[]>([]),[reparseBusy,setReparseBusy]=useState(''),[reparseError,setReparseError]=useState('')
  const selectionEpoch=useRef(0)
  const reparseInfo=info?.reparse as ReparseCapability|undefined
  const sourceSession=verdict?.evidence_context?.session_id,sourceMessage=verdict?.evidence_context?.message_id
  const evidenceUrl=evidence&&runId?`/api/evidence/${encodeURIComponent(evidence)}?run=${encodeURIComponent(runId)}${sourceSession&&sourceMessage?`&session=${encodeURIComponent(sourceSession)}&message=${encodeURIComponent(sourceMessage)}`:''}`:undefined
  useEffect(()=>{setImageUrl(undefined);if(!evidenceUrl||loadedEvidenceUrl!==evidenceUrl||!info||info.image_available===false||info.page===null)return;const controller=new AbortController();let url:string|undefined;fetchBlob(evidenceUrl+'&format=png',controller.signal).then(blob=>{if(controller.signal.aborted)return;url=URL.createObjectURL(blob);setImageUrl(url)}).catch(e=>{if(!controller.signal.aborted)setError((e as Error).message)});return()=>{controller.abort();if(url)URL.revokeObjectURL(url)}},[evidenceUrl,info,loadedEvidenceUrl])
  useEffect(()=>{if(!evidenceUrl)return;const controller=new AbortController();setInfo(undefined);setLoadedEvidenceUrl(undefined);setError('');api<Record<string,unknown>>(evidenceUrl,'GET',undefined,controller.signal).then(value=>{if(!controller.signal.aborted){setInfo(value);setLoadedEvidenceUrl(evidenceUrl)}}).catch(e=>{if(!controller.signal.aborted)setError((e as Error).message)});return()=>controller.abort()},[evidenceUrl])
  useEffect(()=>{setEvidence(undefined);setInfo(undefined)},[verdict?.claim_id,runId])
  useEffect(()=>{selectionEpoch.current++;setReparseError('');setReparseBusy('')},[verdict?.claim_id,runId,evidence])
  useEffect(()=>{
    setPackets([])
    if(!runId||!reparseInfo?.available)return
    const controller=new AbortController()
    api<{packets:ReparsePacket[]}>(`/api/runs/${encodeURIComponent(runId)}/reparse-packets`,'GET',undefined,controller.signal)
      .then(result=>{if(!controller.signal.aborted)setPackets(previous=>[...result.packets,...previous.filter(packet=>!result.packets.some(item=>item.packet_id===packet.packet_id))])}).catch(error=>{if(!controller.signal.aborted)setReparseError((error as Error).message)})
    return()=>controller.abort()
  },[runId,reparseInfo?.available])
  async function reparsePage(){
    if(!runId||!reparseInfo?.available||!reparseInfo.report_id||!reparseInfo.page)return
    const epoch=selectionEpoch.current
    setReparseBusy('parsing');setReparseError('')
    try{
      const binding=verdict&&!['source_quote','source_fact'].includes(verdict.status)&&!verdict.claim_id.startsWith('analysis-')?verdict.claim_id:undefined
      const packet=await api<ReparsePacket>(`/api/runs/${encodeURIComponent(runId)}/reparse`,'POST',
        {report_id:reparseInfo.report_id,page:reparseInfo.page,...(binding?{binding_id:binding}:{})})
      if(selectionEpoch.current===epoch)setPackets(previous=>[...previous.filter(item=>item.packet_id!==packet.packet_id),packet])
    }catch(error){if(selectionEpoch.current===epoch)setReparseError((error as Error).message)}
    finally{if(selectionEpoch.current===epoch)setReparseBusy('')}
  }
  function reparsePanel(){
    if(!reparseInfo?.available)return null
    const selected=packets.filter(packet=>packet.proof&&packet.proof.report_id===reparseInfo.report_id&&packet.proof.pages.includes(reparseInfo.page!))
    return <section className="reparse-panel"><h3>页面补证</h3>
      <p className="muted">重新读取该页与相邻页，最多三页。补证包独立保存；原报告保持只读，应用补证会创建新任务。</p>
      <Button size="sm" disabled={!!reparseBusy} onClick={reparsePage}>{reparseBusy==='parsing'?'正在解析页面…':'重新解析该页'}</Button>
      {reparseError&&<p role="alert" className="notice">{reparseError}</p>}
      {runId&&<ReparsePackets key={`${runId}:${reparseInfo.report_id}:${reparseInfo.page}`} runId={runId} packets={selected} onNewRun={onNewRun} disabled={!!reparseBusy}/>}
    </section>
  }
  function openEvidence(id:string){setEvidence(id);dock.openContent({kind:'evidence',contentId:'active-evidence',title:copy.evidence})}
  function tab(tab:TabRecord){if(tab.kind==='evidence')return <div className="inspector-body"><h3>{copy.evidence}</h3>{error&&<div className="notice">{error}</div>}{evidence&&runId&&<><p className="evidence-id">{evidence}</p>{imageUrl&&<img className="evidence-image" alt="财报原始页面与证据定位" src={imageUrl}/>}<p className="muted">{String(info?.source_path||'')} · {typeof info?.page==='number'?`第 ${info.page} 页`:'原文片段定位'}</p>
      {info?.material_kind==='accounting_knowledge'&&<p className="notice">外部会计材料原文；来源、版本与有效性声明尚未独立核验，不属于签发规则或数字判定。</p>}
      {info?.reference_status==='source_definition_unresolved'&&<p className="notice">这是已冻结披露材料的原页。发行人口径或指标定义仍待确认，本引用不构成事实值或核实判定。</p>}
      {typeof info?.quote_text==='string'&&<section><h3>原文片段</h3><pre>{info.quote_text}</pre></section>}
      {info?.material_kind==='accounting_knowledge'&&info?.metadata&&<details><summary>来源声明与版本信息</summary><pre>{JSON.stringify({metadata:info.metadata,effective_status:info.effective_status,knowledge_lock:info.knowledge_lock},null,2)}</pre></details>}
      {reparsePanel()}</>}</div>
    if(tab.kind==='trace')return <div className="inspector-body"><h3>计划与工具活动</h3>{events.slice(-100).map((event,index)=><div key={index} className="trace-row"><b>{event.name||event.tool||event.event||'执行事件'}</b><small>{event.role} {label(event.status||'')} {event.message||event.reason||''}</small></div>)}</div>
    if(!verdict)return <div className="inspector-body muted">选择一条陈述，查看结论、计算与来源。</div>
    if(verdict.display_status==='pending'||verdict.status==='pending')return <div className="inspector-body"><Tag tone="neutral">待处理</Tag><MarkdownText text={verdict.claim_text||verdict.claim_id} labels={markdownLabels}/><div className="notice">这条陈述尚未完成绑定或金融工具核查。当前没有可交付的内核结论，任务恢复后继续处理。</div></div>
    if(verdict.status==='source_fact')return <div className="inspector-body"><Tag tone="neutral">原页补证事实</Tag><MarkdownText text={verdict.claim_text||'原 PDF 补证事实'} labels={markdownLabels}/><p className="notice">{verdict.reason||'原页补证材料；此面板不生成独立研报判定。'}</p><h3>原页事实</h3><p>{verdict.display_value||'请查看原事实记录'}</p>{verdict.operands?.length?<details><summary>事实维度与来源记录</summary><pre>{JSON.stringify(verdict.operands,null,2)}</pre></details>:null}<h3>来源证据</h3>{verdict.evidence.map(id=><button className="evidence-link" key={id} onClick={()=>openEvidence(id)}>{id}</button>)}<h3>解析依据</h3>{verdict.basis.map((basis,index)=><p key={index}>{basis}</p>)}</div>
    if(verdict.status==='source_quote')return <div className="inspector-body"><Tag tone="neutral">原文引用</Tag><MarkdownText text={verdict.claim_text||'锁定材料原文引用'} labels={markdownLabels}/><p className="notice">{verdict.reason}</p><h3>来源证据</h3>{verdict.evidence.map(id=><button className="evidence-link" key={id} onClick={()=>openEvidence(id)}>{id}</button>)}</div>
    const correction=verdict.correction
    return <div className="inspector-body"><Tag tone={verdict.status==='verified'?'success':verdict.status==='violated'?'danger':'warning'}>{label(verdict.status)} {verdict.error_type} {verdict.abstention_code}</Tag><MarkdownText text={verdict.claim_text||verdict.claim_id} labels={markdownLabels}/>{verdict.reason&&<div className="notice">{verdict.reason}</div>}
      <h3>数字与计算</h3><dl><dt>原文数值</dt><dd>{verdict.observed??'未知'}</dd><dt>内核正确值</dt><dd>{verdict.display_value??verdict.expected??'暂无可核实值'}</dd><dt>计算式</dt><dd>{verdict.formula||'直接披露值／材料不足'}</dd></dl>{verdict.operands?.length? <details><summary>操作数与证据</summary><pre>{JSON.stringify(verdict.operands,null,2)}</pre></details>:null}
      {verdict.precision_note&&<p className="notice">{verdict.precision_note}</p>}
      {verdict.verification_basis==='explicit_disclosed_table_comparison'&&<p className="notice">已核查明确引用表的转述。来源由报告版本、期间与表内身份确定；未据此推断合并或母公司口径。</p>}
      {verdict.display_precision_note&&<p className="notice">{verdict.display_precision_note}</p>}
      {verdict.claim_id.startsWith('analysis-')&&verdict.expected!==undefined&&<details><summary>完整计算值（审计）</summary><p className="muted">计算值保留完整精度；可支持的展示精度以来源及其区间为准。</p><pre>{verdict.expected}</pre></details>}
      {(verdict.claim_interval||verdict.expected_interval)&&<details><summary>披露精度与比较区间</summary><p className="muted">{verdict.verification_basis||'依来源和陈述的单位及小数位比较区间。'}</p><pre>{JSON.stringify({原文区间:verdict.claim_interval,来源区间:verdict.expected_interval,来源精度:verdict.source_precision},null,2)}</pre></details>}
      {!!verdict.diagnostic_hypotheses?.length&&<details><summary>待证实的错误线索</summary><p className="muted">这些线索尚不足以证明期间、口径或版本错误。</p><pre>{JSON.stringify(verdict.diagnostic_hypotheses,null,2)}</pre></details>}
      {!!verdict.blockers?.length&&<details open><summary>全部核查阻断与待补依据</summary>{verdict.blockers.map((item,index)=><article key={index}><b>{item.code} {item.account} {item.period}</b><p>{item.reason}</p>{item.evidence?.map(id=><button className="evidence-link" key={id} onClick={()=>openEvidence(id)}>{id}</button>)}</article>)}</details>}
      {verdict.source_dimensions&&Object.keys(verdict.source_dimensions).length>0&&<details><summary>口径、业务维度与方法来源</summary><pre>{JSON.stringify(verdict.source_dimensions,null,2)}</pre></details>}
      <h3>来源证据</h3>{verdict.evidence?.map(id=><button className="evidence-link" key={id} onClick={()=>openEvidence(id)}>{id}</button>)}{!verdict.evidence?.length&&<p className="muted">该条未获得足够来源证据。</p>}
      <h3>规则依据</h3>{verdict.basis?.map((basis,index)=><p key={index}>{basis}</p>)}{correction&&<><h3>可替换文本与修正</h3><dl>{[['correct_value','正确值'],['source','来源与证据'],['calculation','计算式'],['basis','规则依据'],['replacement_text','替换文本']].map(([key,title])=><div key={key}><dt>{title}</dt><dd>{correction[key]||'暂无'}</dd></div>)}</dl></>}
    </div>
  }
  return <div className="inspector" style={{position:'absolute',inset:'0 0 0 auto',width,zIndex:10}}><DockSurface state={state} canSplit={canSplit} intents={dock} labels={dockLabels} renderTab={tab} canAddTab={()=>false} canCloseTab={tabId=>state.tabs[tabId]?.kind!=='verdict'} chrome={<><Button size="sm" onClick={()=>dock.openContent({kind:'trace',contentId:'activity',title:copy.trace})}>活动</Button><Button size="sm" onClick={onClose}>关闭</Button></>}/><FloatLayer state={state} intents={dock} labels={dockLabels} renderTab={tab}/></div>
}
