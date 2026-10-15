import {useCallback,useEffect,useMemo,useRef,useState} from 'react'
import {Button} from '../vendor/harness/packages/client/ui-primitives/src/Button'
import {Tag} from '../vendor/harness/packages/client/ui-primitives/src/Tag'
import {AppFrame} from './HarnessFrame'
import type {FrameLayout} from './HarnessFrame'
import {api,setLocalBearer,ApiError,downloadFile} from './api'
import {copy,label} from './i18n'
import type {Config,ConfigEnvelope,Results,Run,RunEvent,Session,Verdict,ScopeClarification} from './types'
import {TaskForm} from './TaskForm'
import {ResultsView} from './ResultsView'
import {Settings} from './Settings'
import {Conversation} from './Conversation'
import {Inspector} from './Inspector'
import {ReparseProposals} from './ReparseProposals'

const activeStatuses=['queued','running']
const resultStatuses=['completed','partial','cancelled','interrupted','failed']

export function App() {
  const [config,setConfig]=useState<Config>()
  const [runs,setRuns]=useState<Run[]>([]),[sessions,setSessions]=useState<Session[]>([])
  const [current,setCurrent]=useState<Run>(),[data,setData]=useState<Results>()
  const [events,setEvents]=useState<RunEvent[]>([]),[selected,setSelected]=useState<Verdict>()
  const [settings,setSettings]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('')
  const [chat,setChat]=useState(false),[runSessionIds,setRunSessionIds]=useState<Record<string,string>>({})
  const sessionId=current?runSessionIds[current.id]:undefined
  const refreshEpoch=useRef(0)
  const [needsAuth,setNeedsAuth]=useState(false),[bearer,setBearer]=useState(''),[pollEpoch,setPollEpoch]=useState(0)
  const [layout,setLayout]=useState<FrameLayout>({sidebar:260,viewportWidth:window.innerWidth,narrowExpanded:false,rightbar:400,rightbarShown:false,rightbarTrack:false,rightbarFullscreen:false,rightbarInstant:false})
  const showError=useCallback((message:string)=>setError(message),[])

  const refresh=useCallback(async()=>{
    const epoch=++refreshEpoch.current
    try {
      const [envelope,runList,sessionList]=await Promise.all([
        api<ConfigEnvelope>('/api/config'),api<{runs:Run[]}>('/api/runs'),api<{sessions:Session[]}>('/api/sessions')])
      if(epoch!==refreshEpoch.current)return
      setConfig(envelope.config);setRuns(runList.runs);setSessions(sessionList.sessions);setNeedsAuth(false)
      setRunSessionIds(previous=>{
        const next:Record<string,string>={}
        for(const session of sessionList.sessions){if(!next[session.run_id])next[session.run_id]=session.session_id}
        for(const [runId,id] of Object.entries(previous)){
          if(sessionList.sessions.some(session=>session.run_id===runId&&session.session_id===id))next[runId]=id
        }
        return next
      })
    } catch(e) {if(e instanceof ApiError&&e.status===401)setNeedsAuth(true);else setError((e as Error).message)}
  },[])
  const rememberSession=useCallback((session:Session)=>{
    // A conversation belongs to one immutable material task. Keeping the
    // selection outside its mounted panel preserves history across view and
    // task switches, while an authoritative refresh prunes deleted sessions.
    ++refreshEpoch.current
    setRunSessionIds(previous=>previous[session.run_id]&&previous[session.run_id]!==session.session_id?
      previous:{...previous,[session.run_id]:session.session_id})
    setSessions(previous=>[session,...previous.filter(item=>item.session_id!==session.session_id)])
  },[])
  useEffect(()=>{void refresh()},[refresh])
  const actions=useMemo(()=>({
    setViewportWidth:(width:number)=>setLayout(value=>value.viewportWidth===width?value:{...value,viewportWidth:width}),
    setSidebar:(width:number)=>setLayout(value=>({...value,sidebar:Math.max(180,Math.min(380,width))})),
    setRightbar:(width:number)=>setLayout(value=>({...value,rightbar:Math.max(300,Math.min(value.viewportWidth*.65,width))})),
    setNarrowExpanded:(value:boolean)=>setLayout(v=>({...v,narrowExpanded:value}))
  }),[])

  useEffect(()=>{
    if(!current)return
    const runId=current.id,controller=new AbortController()
    let stopped=false,timer:ReturnType<typeof setTimeout>|undefined
    setData(undefined);setSelected(undefined);setEvents([])
    async function poll(){
      try {
        const [run,stream]=await Promise.all([
          api<Run>(`/api/runs/${runId}`,'GET',undefined,controller.signal),
          api<{events:RunEvent[]}>(`/api/runs/${runId}/events`,'GET',undefined,controller.signal)])
        if(stopped)return
        setCurrent(run);setEvents(stream.events)
        if(activeStatuses.includes(run.status))timer=setTimeout(poll,800)
        else if(resultStatuses.includes(run.status)){
          const result=await api<Results|Run>(`/api/runs/${runId}/results`,'GET',undefined,controller.signal)
          if(!stopped&&'results' in result)setData(result)
          if(!stopped)void refresh()
        }
        if(!stopped&&run.error)setError(run.error)
      } catch(e){
        if(stopped)return
        if(e instanceof ApiError&&e.status===401)setNeedsAuth(true)
        else {setError((e as Error).message);timer=setTimeout(poll,2500)}
      }
    }
    void poll()
    return()=>{stopped=true;controller.abort();if(timer)clearTimeout(timer)}
  },[current?.id,pollEpoch,refresh])

  function newTask(){setCurrent(undefined);setData(undefined);setChat(false);setSelected(undefined);setEvents([]);setError('');setNotice('');setLayout(value=>({...value,rightbarShown:false,rightbarTrack:false}));void refresh()}
  function openRun(run:Run,sid?:string){setCurrent(run);setChat(Boolean(sid));if(sid)setRunSessionIds(previous=>({...previous,[run.id]:sid}));setError('');setNotice('')}
  function select(row:Verdict){setSelected(row);setLayout(value=>({...value,rightbarShown:true,rightbarTrack:true}))}
  async function replay(mode='kernel'){
    if(!current)return
    try {setNotice('正在重放…');const value=await api<Record<string,unknown>>('/api/replay','POST',{run:current.out,mode});setNotice('重放记录：'+JSON.stringify(value))}
    catch(e){setNotice('');showError((e as Error).message)}
  }
  async function resume(){
    if(!current)return
    try {setCurrent(await api<Run>(`/api/runs/${current.id}/resume`,'POST',{}));setPollEpoch(value=>value+1);setError('')}
    catch(e){showError((e as Error).message)}
  }
  async function rerun(clarification?:string|ScopeClarification[]){
    if(!current)return
    const overrides=clarification===undefined?{}:typeof clarification==='string'?{scope_declaration:clarification,scope_clarifications:[]}:{scope_declaration:'',scope_clarifications:[...(data?.scope_clarifications||[]),...clarification]}
    try {const run=await api<Run>(`/api/runs/${current.id}/rerun`,'POST',overrides);openRun(run);void refresh();setNotice('已按当前设置新建完整核查任务，旧结果保持冻结。')}
    catch(e){showError((e as Error).message)}
  }

  const sidebar=(collapsed:boolean,narrow=false)=><aside className={'sidebar '+(collapsed?'collapsed':'')}>
    <header><button className="brand" onClick={newTask}>{collapsed?'镜':'明镜'}{!collapsed&&<span>金融 Agent</span>}</button>{narrow&&collapsed&&<Button size="sm" aria-label="展开边栏" onClick={()=>actions.setNarrowExpanded(true)}>›</Button>}</header>
    <Button variant="outline" onClick={newTask}>{collapsed?'＋':copy.newTask}</Button>
    {!collapsed&&<><div className="sidebar-heading">{copy.history}{narrow&&<Button size="sm" aria-label="收起边栏" onClick={()=>actions.setNarrowExpanded(false)}>‹</Button>}<Button size="sm" aria-label="刷新历史" onClick={refresh}>↻</Button></div>
      <div className="history-list">{runs.map(run=><button key={run.id} className={'history-row '+(run.id===current?.id?'active':'')} onClick={()=>openRun(run)}>
        <span>{String(run.request?.company||run.id)}</span><small>{label(run.status)} · {run.id.slice(-8)}</small></button>)}
        {sessions.map(session=><button key={session.session_id} className="history-row" onClick={()=>{const run=runs.find(item=>item.id===session.run_id);if(run)openRun(run,session.session_id)}}>
          <span>材料追问</span><small>{label(session.status)} · {session.session_id.slice(-8)}</small></button>)}
        {!runs.length&&<p className="muted">{copy.empty}</p>}</div></>}
    <footer><Button onClick={()=>setSettings(true)}>{collapsed?'⚙':copy.settings}</Button>
      <Button onClick={()=>{document.body.toggleAttribute('data-ds-dark-theme')}}>{collapsed?'◐':'切换明暗主题'}</Button>
      <small>{!collapsed&&'金融工具与确定性内核'}</small></footer>
  </aside>
  const phase=[...events].reverse().find(event=>event.percent!==undefined)
  if(needsAuth)return <div className="login"><h1>{copy.title}</h1><label>本地服务访问令牌<input type="password" value={bearer} onChange={e=>setBearer(e.target.value)}/></label>
    <Button variant="primary" onClick={()=>{setLocalBearer(bearer);setBearer('');setPollEpoch(value=>value+1);void refresh()}}>连接本地服务</Button></div>
  if(!config)return <div className="login"><h1>{copy.title}</h1><p>{error||'正在连接本地金融服务…'}</p><Button onClick={refresh}>重试连接</Button></div>

  return <><AppFrame useStore={selector=>selector({layoutInfo:layout})} usePanelInfo={selector=>selector({activePanelId:null})} actions={actions} t={()=>copy.title} renderSlot={(slot,props)=>{
    if(slot==='sidebar')return sidebar(Boolean(props.collapsed),Boolean(props.narrow))
    if(slot==='rightbar')return layout.rightbarShown?<Inspector width={Number(props.width)||Math.min(layout.viewportWidth,400)} verdict={selected} runId={current?.id} events={events} onNewRun={run=>{openRun(run);setSelected(undefined);setLayout(value=>({...value,rightbarShown:false,rightbarTrack:false}));void refresh()}} onClose={()=>setLayout(value=>({...value,rightbarShown:false,rightbarTrack:false}))}/>:null
    if(slot==='shell.overlay')return null
    return <main className="main"><header className="main-header">
      <div><b>{current?String(current.request?.company||'金融任务'):copy.newTask}</b>{current&&<Tag tone={current.status==='failed'?'danger':current.status==='partial'?'warning':'neutral'}>{label(current.status)}</Tag>}</div>
      <div className="header-actions">{current&&<>
        <Button size="sm" disabled={!data} onClick={()=>setChat(!chat)}>{chat?'核查结果':copy.chat}</Button>
        <Button size="sm" onClick={()=>setLayout(value=>({...value,rightbarShown:!value.rightbarShown,rightbarTrack:!value.rightbarShown}))}>证据面板</Button>
        {activeStatuses.includes(current.status)?<Button size="sm" onClick={async()=>{try{await api(`/api/runs/${current.id}/cancel`,'POST',{})}catch(e){showError((e as Error).message)}}}>停止任务</Button>:<>
          <Button size="sm" disabled={!data} onClick={()=>replay()}>内核重放</Button>
          {data?.mode==='harness'&&<Button size="sm" onClick={()=>replay('agent')}>Agent 重放</Button>}
          <Button size="sm" disabled={!['completed','partial'].includes(current.status)} onClick={async()=>{try{await downloadFile(`/api/runs/${current.id}/export`,`MingJing-${current.id}.zip`)}catch(e){showError((e as Error).message)}}}>导出报告</Button></>}
        {(current.can_resume??['interrupted','cancelled','failed'].includes(current.status))&&<Button size="sm" onClick={resume}>恢复任务</Button>}
        {current.can_rerun&&<Button size="sm" onClick={()=>rerun()}>按当前设置重新核查</Button>}
      </>}</div></header>
      {(error||notice)&&<div role={error?'alert':'status'} className="notice dismissable"><span>{error||notice}</span><Button size="sm" onClick={()=>{setError('');setNotice('')}}>关闭</Button></div>}
      {current&&activeStatuses.includes(current.status)&&<div className="run-progress"><progress max="100" value={phase?.percent||0}/><span>{phase?.name||'正在运行金融任务'}</span><small>{events.filter(event=>event.event==='tool').length} 次已记录工具活动</small></div>}
      {!current?<TaskForm config={config} onError={showError} onRun={run=>{openRun(run);void refresh()}}/>:
        chat&&data?<Conversation key={current.id} runId={current.id} initialId={sessionId} onSessionChanged={rememberSession} onError={showError} onSelect={select} onNewRun={id=>{void api<Run>(`/api/runs/${id}`).then(run=>{openRun(run);void refresh()}).catch(e=>showError((e as Error).message))}}/>:
        data?<><ResultsView key={current.id} data={data} onSelect={select} selected={selected?.claim_id} onClarifyScope={current.can_rerun?scope=>{void rerun(scope)}:undefined} onClarifyGroups={current.can_rerun?groups=>{void rerun(groups)}:undefined}/><ReparseProposals key={'reparse-'+current.id} runId={current.id} onNewRun={run=>{openRun(run);setSelected(undefined);setLayout(value=>({...value,rightbarShown:false,rightbarTrack:false}));void refresh()}}/></>:
        <div className="waiting"><h2>{label(current.status)}</h2><p className="muted">{phase?.name||'等待任务生成可核查结果。'}</p><div className="live-activities">{events.slice(-20).map((event,index)=><p key={index}>{event.name||event.tool||event.event} {event.role} {event.message||event.reason||''}</p>)}</div></div>}
    </main>
  }}/>{settings&&<Settings config={config} onSaved={setConfig} onClose={()=>setSettings(false)}/>}</>
}
