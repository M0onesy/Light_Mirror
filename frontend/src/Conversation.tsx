import {useCallback,useEffect,useRef,useState} from 'react'
import {Button} from '../vendor/harness/packages/client/ui-primitives/src/Button'
import {DisclosureRow} from '../vendor/harness/packages/client/ui-primitives/src/DisclosureRow'
import {IconSearchOutline16} from '../vendor/harness/packages/client/ui-primitives/src/icons/index'
import {MarkdownText} from '../vendor/harness/packages/client/ui-primitives/src/markdown/MarkdownText'
import {api} from './api'
import type {Session,SourceReference,Verdict} from './types'
import {label,markdownLabels} from './i18n'

const REFERENCE_PREVIEW_LIMIT=3

function sourceSections(text:string):{title:string;text:string}[]|null {
  const headings=[...text.matchAll(/^#[ \t]+([^\r\n]+?)[ \t]*\r?$/gm)]
  const allowed=new Set(['原文','财报分析','核查结果','证据','来源与证据','规则依据','公式','待补材料','提示',
    '明镜核查报告','核查报告','核查结果如下','修改建议','正确值','替换文本',
    '营业收入','归母净利润','净利润','扣非净利润','经营现金流','毛利率','净利率','资产负债率'])
  if(!headings.length||text.slice(0,headings[0].index).trim()||headings.some(heading=>!allowed.has(heading[1])))return null
  const sections:{title:string;text:string}[]=[]
  for(let index=0;index<headings.length;index++){
    const start=index===0?0:headings[index].index!,end=headings[index+1]?.index??text.length
    const section={title:headings[index][1],text:text.slice(start,end)}
    const previous=sections[sections.length-1]
    if(previous?.title==='原文'&&section.title==='原文')previous.text+=section.text
    else sections.push(section)
  }
  return sections
}

function SourceQuoteReader({text}:{text:string}) {
  const [open,setOpen]=useState(false)
  return <div className="source-quote-message">
    <p className="source-quote-label">原文引用，未作数字核查</p>
    <p className="muted">较长的材料原文可展开完整阅读；引用中的金额、日期与结论尚未经过数字核查。</p>
    <DisclosureRow icon={<IconSearchOutline16/>} title={open?'收起完整原文':'展开完整原文'} open={open}
      expandable expandOnRowClick onToggle={()=>setOpen(!open)} className="source-quote-disclosure">
      <div className="source-quote-reader" role="region" aria-label="完整原文引用"><MarkdownText text={text} labels={markdownLabels}/></div>
    </DisclosureRow>
  </div>
}

function MessageContent({message,sessionId,onSelect}:{message:Session['messages'][number];sessionId:string;onSelect:(result:Verdict)=>void}) {
  const [allEvidence,setAllEvidence]=useState(false)
  const [replayBusy,setReplayBusy]=useState(false),[replayResult,setReplayResult]=useState('')
  async function replay(){
    if(!message.message_id)return
    setReplayBusy(true);setReplayResult('')
    try{
      const result=await api<{status:string;real_harness_loop:boolean;network_calls:number;reason?:string}>(
        `/api/sessions/${sessionId}/messages/${message.message_id}/replay`,'POST',{})
      setReplayResult(result.status==='passed'&&result.real_harness_loop&&result.network_calls===0?
        '离线重放通过：原生工具循环、回答、引用及工具轨迹一致，未访问网络。':`重放未通过：${result.reason||result.status}`)
    }catch(error){setReplayResult(`重放失败：${(error as Error).message}`)}
    finally{setReplayBusy(false)}
  }
  const references=message.source_references||[]
  // Only top-level host headings partition an answer. Quoted source headings
  // are escaped by the host; nested financial headings never form a boundary.
  // Unknown structure remains fully visible, as do all numeric answer sections.
  const sections=message.role==='assistant'&&references.length>0&&
    references.every(reference=>['unverified_source_quotation','source_quote'].includes(reference.reference_status||''))?
    sourceSections(message.text):null
  const listed=references.map((reference,index)=>({reference,index}))
  const visible=allEvidence?listed:listed.slice(0,REFERENCE_PREVIEW_LIMIT)
  const groups=new Map<string,{title:string;page:number|null;items:{reference:SourceReference;index:number}[]}>()
  for(const item of visible){
    const reference=item.reference,name=reference.name||reference.title||'锁定材料'
    const key=(reference.doc_sha256||reference.document_sha256||name)+':'+reference.page
    const group=groups.get(key)||{title:name,page:reference.page,items:[]}
    group.items.push(item);groups.set(key,group)
  }
  const ordered=[...groups.values()].sort((left,right)=>left.title.localeCompare(right.title,'zh-CN')||(left.page??0)-(right.page??0))
  function select(reference:SourceReference,index:number){
    onSelect({claim_id:`source-${sessionId}-${message.message_id}-${index}`,status:'source_quote',
      claim_text:reference.title||reference.name||'锁定材料原文引用',
      reason:reference.material_kind==='accounting_knowledge'?'这是外部会计材料原文；来源、版本与有效性声明尚未独立核验，不属于签发规则或数字判定。':'这是冻结材料的原文引用，未作数字核查；不能据此认定原文中的数字正确。',
      evidence:[reference.evidence_id],basis:[reference.page===null?'冻结文档哈希与原文片段定位':'冻结文档、原文片段与页级定位'],
      evidence_context:{session_id:sessionId,message_id:message.message_id!}})
  }
  function evidenceButton(reference:SourceReference,index:number,fragment?:number){
    return <Button key={`${reference.evidence_id}-${index}`} size="sm" className="source-reference-link"
      title={reference.evidence_id} data-evidence-id={reference.evidence_id} onClick={()=>select(reference,index)}>
      查看原文证据 · {reference.page===null?'文本片段':`第 ${reference.page} 页`}{fragment!==undefined&&<> · 片段 {fragment}</>}
    </Button>
  }
  return <>
    {sections?sections.map((section,index)=>section.title==='原文'&&
      [...section.text.matchAll(/^#{1,6}[ \t]+([^\r\n]+?)[ \t]*\r?$/gm)].every(heading=>heading[1]==='原文')&&
      (references.length>REFERENCE_PREVIEW_LIMIT||section.text.length>3000)?
      <SourceQuoteReader key={index} text={section.text}/>:<MarkdownText key={index} text={section.text} labels={markdownLabels}/>):
      <MarkdownText text={message.text} labels={markdownLabels}/>}
    {message.role==='assistant'&&message.agent_message_manifest_sha256&&<div className="message-replay">
      <Button size="sm" disabled={replayBusy} onClick={replay}>{replayBusy?'正在离线重放…':'重放这条回答'}</Button>
      {replayResult&&<p role="status" className="muted">{replayResult}</p>}
    </div>}
    {!!message.message_id&&!!references.length&&<div className="source-quote-evidence">
      {references.length>REFERENCE_PREVIEW_LIMIT?<>
        <p className="muted">原文证据 · {references.length} 条{!allEvidence&&<>，先显示 {REFERENCE_PREVIEW_LIMIT} 条</>}</p>
        <div className="source-reference-groups">{ordered.map((group,index)=><section className="source-reference-group" key={index}>
          <h4>{group.title} · {group.page===null?'文本片段':`第 ${group.page} 页`}</h4>
          <div className="source-reference-buttons">{group.items.map(({reference,index},fragment)=>evidenceButton(reference,index,fragment+1))}</div>
        </section>)}</div>
        <Button size="sm" aria-expanded={allEvidence} onClick={()=>setAllEvidence(!allEvidence)}>
          {allEvidence?'收起证据':`展开全部 ${references.length} 条证据`}
        </Button>
      </>:references.map((reference,index)=>evidenceButton(reference,index))}
    </div>}
  </>
}

export function Conversation({runId,initialId,onError,onNewRun,onSelect,onSessionChanged}:{runId:string;initialId?:string;onError:(message:string)=>void;onNewRun:(runId:string)=>void;onSelect:(result:Verdict)=>void;onSessionChanged?:(session:Session)=>void}) {
  const [session,setSession]=useState<Session|null>(null),[question,setQuestion]=useState(''),[submitting,setSubmitting]=useState(false),[materials,setMaterials]=useState(false),[corpus,setCorpus]=useState('')
  const [scope,setScope]=useState('')
  const sessionRef=useRef<Session|null>(null)
  const receiveSession=useCallback((state:Session)=>{
    if(state.run_id!==runId)throw Error('会话与当前材料任务不匹配，已拒绝载入。')
    sessionRef.current=state;setSession(state);onSessionChanged?.(state)
  },[runId,onSessionChanged])
  useEffect(()=>{
    if(initialId===sessionRef.current?.session_id)return
    sessionRef.current=null;setSession(null)
    if(!initialId)return
    let stopped=false;const controller=new AbortController()
    api<Session>(`/api/sessions/${initialId}`,'GET',undefined,controller.signal)
      .then(state=>{if(!stopped)receiveSession(state)}).catch(error=>{if(!stopped)onError((error as Error).message)})
    return()=>{stopped=true;controller.abort()}
  },[initialId,runId,receiveSession,onError])
  useEffect(()=>{if(!session||session.status!=='running')return;let stopped=false;let timer:ReturnType<typeof setTimeout>|undefined;const controller=new AbortController();async function poll(){try{const state=await api<Session>(`/api/sessions/${session!.session_id}`,'GET',undefined,controller.signal);if(stopped)return;receiveSession(state);if(state.status==='running')timer=setTimeout(poll,800)}catch(e){if(!stopped)onError((e as Error).message)}}timer=setTimeout(poll,800);return()=>{stopped=true;controller.abort();if(timer)clearTimeout(timer)}},[session?.session_id,session?.status,receiveSession,onError])
  async function submit(){setSubmitting(true);try{const current=sessionRef.current||await api<Session>('/api/sessions','POST',{run_id:runId});receiveSession(current);receiveSession(await api<Session>(`/api/sessions/${current.session_id}/messages`,'POST',{question}));setQuestion('')}catch(e){onError((e as Error).message)}finally{setSubmitting(false)}}
  async function addMaterials(){
    setSubmitting(true)
    try{
      const current=sessionRef.current||await api<Session>('/api/sessions','POST',{run_id:runId})
      receiveSession(current)
      const payload={...(corpus.trim()?{corpus:corpus.trim()}:{}),...(scope?{scope_declaration:scope}:{})}
      const updated=await api<{run_id:string;new_version:boolean}>(`/api/sessions/${current.session_id}/materials`,'POST',payload)
      setMaterials(false);onNewRun(updated.run_id)
    }catch(e){onError((e as Error).message)}finally{setSubmitting(false)}
  }
  async function pickMaterials(kind:'file'|'dir'){
    try{const selected=await api<{path?:string}>('/api/paths/pick','POST',{kind});if(selected.path)setCorpus(selected.path)}catch(e){onError((e as Error).message)}
  }
  const busy=submitting||session?.status==='running'
  const activities=session?.activities||session?.events||[]
  return <div className="conversation"><header><h2>同一材料内追问</h2><p className="muted">回答引用冻结材料、证据和内核结果。{session&&<> · {label(session.status)}</>}</p></header><div className="messages">{session?.messages.map((message,index)=><article className={'message '+message.role} key={message.message_id||index}><div className="speaker">{message.role==='user'?'你':message.role==='assistant'?'明镜':'任务提示'}</div><MessageContent message={message} sessionId={session.session_id} onSelect={onSelect}/></article>)}{!session?.messages.length&&<p className="muted">例如：营业收入同比使用了哪两期数据？经营现金流与利润为什么背离？会计政策变更的披露原文在哪里？</p>}{busy&&<div className="message system">正在通过金融工具查证…</div>}</div>
    {!!activities.length&&<details className="conversation-activities"><summary>本条消息的工具与执行活动（{activities.length}）</summary>{activities.slice(-20).map((event,index)=><p key={index}>{event.role} · {event.tool||event.name||event.event} · {label(event.status||'')}</p>)}</details>}
    <div className="composer"><textarea aria-label="金融问题" maxLength={3000} value={question} onChange={e=>setQuestion(e.target.value)} placeholder="输入材料问题，Ctrl+Enter 发送" onKeyDown={e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)&&question.trim()&&!busy){e.preventDefault();void submit()}}}/><div className="form-actions"><Button onClick={()=>setMaterials(!materials)} disabled={busy}>补充材料</Button>{session&&['interrupted','cancelled','failed'].includes(session.status)&&<Button disabled={busy} onClick={async()=>{try{receiveSession(await api<Session>(`/api/sessions/${session.session_id}/resume`,'POST',{}))}catch(e){onError((e as Error).message)}}}>恢复会话</Button>}{busy&&session?<Button onClick={async()=>{try{receiveSession(await api<Session>(`/api/sessions/${session.session_id}/cancel`,'POST',{}))}catch(e){onError((e as Error).message)}}}>停止</Button>:<Button variant="primary" disabled={!question.trim()||busy} onClick={submit}>发送问题 ↑</Button>}</div></div>
    {materials&&<div className="form-card"><label>追加财报路径<div className="path-row"><input aria-label="追加财报路径" value={corpus} onChange={e=>setCorpus(e.target.value)}/><Button onClick={()=>pickMaterials('file')}>选择文件</Button><Button onClick={()=>pickMaterials('dir')}>选择目录</Button></div></label>
      <label>补充口径声明<select aria-label="补充口径声明" value={scope} onChange={e=>setScope(e.target.value)}><option value="">保留当前口径</option><option value="合并">我明确声明使用合并口径</option><option value="母公司">我明确声明使用母公司口径</option></select></label>
      <p className="muted">追加财报与已有材料一起核查，建立新资源锁和新任务版本。口径声明作为用户补充归档；与原文明示口径冲突时拒绝覆盖。</p><Button disabled={(!corpus.trim()&&!scope)||busy} onClick={addMaterials}>补证后重查</Button></div>}
  </div>
}
