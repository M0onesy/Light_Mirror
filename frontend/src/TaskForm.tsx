import {useEffect,useRef,useState} from 'react'
import {Button} from '../vendor/harness/packages/client/ui-primitives/src/Button'
import {api} from './api'
import type {Config,Run} from './types'
import {copy} from './i18n'

function normalizeDate(v:string):string|null{const s=String(v??'').trim();if(!s)return '';const m=s.match(/^(\d{4})[-/]?(\d{1,2})[-/]?(\d{1,2})$/);if(!m)return null;const year=+m[1],month=+m[2],day=+m[3];if(month<1||month>12||day<1||day>31)return null;const d=new Date(Date.UTC(year,month-1,day));if(d.getUTCFullYear()!==year||d.getUTCMonth()!==month-1||d.getUTCDate()!==day)return null;return `${m[1]}-${String(month).padStart(2,'0')}-${String(day).padStart(2,'0')}`}

export function TaskForm({config,onRun,onError}:{config:Config;onRun:(run:Run)=>void;onError:(message:string)=>void}) {
  const [task,setTask]=useState({task_type:'check',draft:config.workspace.draft_path||'',corpus:config.workspace.corpus_root||'',company:config.run.company||'',period:config.run.period||'',report_date:config.run.report_date||'',scope_declaration:'',mode:config.run.mode==='harness'?'harness':'kernel'})
  const [busy,setBusy]=useState(false)
  useEffect(()=>{setTask(current=>({...current,draft:config.workspace.draft_path||current.draft,corpus:config.workspace.corpus_root||current.corpus}))},[config.workspace.draft_path,config.workspace.corpus_root])
  const dateRef=useRef<HTMLInputElement>(null)
  function commitDate(raw:string){const n=normalizeDate(raw);if(n===null){onError('日期格式应为 YYYY-MM-DD');setTask(current=>({...current,report_date:''}));return}setTask(current=>({...current,report_date:n}))}
  function pickDate(){const el=dateRef.current;if(!el)return;el.value=task.report_date||'';const native=el as HTMLInputElement&{showPicker?:()=>void};if(typeof native.showPicker==='function'){try{native.showPicker()}catch{el.click()}}else{el.click()}}
  async function begin(demo=false) {setBusy(true);try{const request=demo?await api<Record<string,unknown>>('/api/demo','POST',{}):task;onRun(await api<Run>('/api/run','POST',request))}catch(e){onError((e as Error).message)}finally{setBusy(false)}}
  async function browse(field:'draft'|'corpus',kind='file') {try{const value=await api<{path?:string;cancelled?:boolean}>('/api/paths/pick','POST',{kind});if(value.path)setTask(current=>({...current,[field]:value.path!}))}catch(e){onError((e as Error).message)}}
  return <div className="task-form"><div className="hero"><div className="logo">明镜</div><h1>{copy.title}</h1><p>{copy.subtitle}</p></div>
    <div className="segmented" role="group" aria-label="金融任务类型">{['check','analysis'].map(type=><Button key={type} variant={task.task_type===type?'primary':'ghost'} onClick={()=>setTask({...task,task_type:type,scope_declaration:type==='analysis'?'合并':''})}>{copy[type as 'check'|'analysis']}</Button>)}</div>
    <div className="form-card"><label>研报草稿{task.task_type==='analysis'?'（可留空）':''}<div className="path-row"><input aria-label="研报草稿" value={task.draft} onChange={e=>setTask({...task,draft:e.target.value})} placeholder="PDF、Markdown 或文本文件"/><Button onClick={()=>browse('draft')}>选择文件</Button></div></label>
    <label>财报材料<div className="path-row"><input aria-label="财报材料" value={task.corpus} onChange={e=>setTask({...task,corpus:e.target.value})} placeholder="年报 PDF 或外部财报目录"/><Button onClick={()=>browse('corpus')}>选择 PDF</Button><Button onClick={()=>browse('corpus','dir')}>选择目录</Button></div></label>
    <div className="form-grid"><label>公司<input value={task.company} onChange={e=>setTask({...task,company:e.target.value})} placeholder="上市公司简称"/></label><label>期间<input value={task.period} onChange={e=>setTask({...task,period:e.target.value})} placeholder="2023Y / 2024H1"/></label><label>研报／分析截止日期<span className="date-field"><input aria-label="研报／分析截止日期" value={task.report_date} placeholder="YYYY-MM-DD" inputMode="numeric" autoComplete="off" onChange={e=>setTask({...task,report_date:e.target.value})} onBlur={e=>commitDate(e.target.value)}/><Button onClick={pickDate} aria-label="打开日历选择日期">📅</Button><input ref={dateRef} type="date" className="date-native-hidden" tabIndex={-1} aria-hidden="true" onChange={e=>setTask(current=>({...current,report_date:e.target.value}))}/></span></label><label>执行方式<select value={task.mode} onChange={e=>setTask({...task,mode:e.target.value})}><option value="kernel">离线确定性内核</option><option value="harness">Harness 金融 Agent</option></select></label></div>
    {task.task_type==='check'&&<label>研报口径澄清<select aria-label="研报口径澄清" value={task.scope_declaration} onChange={e=>setTask({...task,scope_declaration:e.target.value})}><option value="">仅依据原文；口径不明时弃权</option><option value="合并">我明确声明本次研报使用合并口径</option><option value="母公司">我明确声明本次研报使用母公司口径</option></select><small className="muted">可选。声明将作为补充材料归档；与原文明确口径冲突时拒绝覆盖。原文数字和历史结果保持冻结。</small></label>}
    {task.task_type==='analysis'&&<label>财报分析口径<select aria-label="财报分析口径" value={task.scope_declaration} onChange={e=>setTask({...task,scope_declaration:e.target.value})}><option value="合并">合并报表</option><option value="母公司">母公司报表</option></select></label>}
    <p className="muted">数字由明镜内核核查，Agent 负责语义绑定与工具检索。材料不完整、定义不清或预算耗尽的部分会明确显示。</p>
    <div className="form-actions"><Button onClick={()=>begin(true)} disabled={busy}>{copy.demo}</Button><Button variant="primary" disabled={busy} onClick={()=>begin()}>{busy?'正在提交…':copy.start}</Button></div></div></div>
}
