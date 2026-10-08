import {useEffect,useRef,useState} from 'react'
import {Button} from '../vendor/harness/packages/client/ui-primitives/src/Button'
import {api,fetchBlob} from './api'
import type {Run} from './types'

export type ReparsePacket={packet_id:string;status:string;new_facts_count:number;conflicts:number;reason?:string;proof?:{report_id:string;pages:number[]}}
type ReparsePreview={packet_id:string;readonly:boolean;facts:{fact_id:string;company:string;title:string;period:string;scope:string;nr:string;version:string;publish_date:string;display_value:string;source_name:string;evidence:string[]}[]}

export function ReparsePackets({runId,packets,onNewRun,disabled=false}:{runId:string;packets:ReparsePacket[];onNewRun?:(run:Run)=>void;disabled?:boolean}) {
  const [previews,setPreviews]=useState<Record<string,ReparsePreview>>({}),[busy,setBusy]=useState(''),[error,setError]=useState('')
  const [target,setTarget]=useState<{packet_id:string;evidence_id:string}>(),[imageUrl,setImageUrl]=useState<string>()
  const generation=useRef(0)
  useEffect(()=>()=>{generation.current++},[runId])
  useEffect(()=>{
    setImageUrl(undefined)
    if(!target)return
    const controller=new AbortController();let url:string|undefined
    const endpoint=`/api/runs/${encodeURIComponent(runId)}/reparse-packets/${encodeURIComponent(target.packet_id)}/evidence/${encodeURIComponent(target.evidence_id)}?format=png`
    fetchBlob(endpoint,controller.signal).then(blob=>{if(controller.signal.aborted)return;url=URL.createObjectURL(blob);setImageUrl(url)})
      .catch(error=>{if(!controller.signal.aborted)setError((error as Error).message)})
    return()=>{controller.abort();if(url)URL.revokeObjectURL(url)}
  },[runId,target?.packet_id,target?.evidence_id])
  async function previewPacket(packet:ReparsePacket){
    if(disabled||busy||packet.status!=='ready'||packet.new_facts_count<1)return
    const epoch=generation.current
    setBusy('preview:'+packet.packet_id);setError('')
    try{
      const preview=await api<ReparsePreview>(`/api/runs/${encodeURIComponent(runId)}/reparse-packets/${encodeURIComponent(packet.packet_id)}`)
      if(generation.current===epoch)setPreviews(previous=>({...previous,[packet.packet_id]:preview}))
    }catch(error){if(generation.current===epoch)setError((error as Error).message)}
    finally{if(generation.current===epoch)setBusy('')}
  }
  async function applyPacket(packet:ReparsePacket){
    if(disabled||busy||packet.status!=='ready'||packet.new_facts_count<1||!onNewRun)return
    const epoch=generation.current
    setBusy(packet.packet_id);setError('')
    try{
      const run=await api<Run>(`/api/runs/${encodeURIComponent(runId)}/rerun`,'POST',{reparse_packet_ids:[packet.packet_id]})
      if(generation.current===epoch)onNewRun(run)
    }catch(error){if(generation.current===epoch)setError((error as Error).message)}
    finally{if(generation.current===epoch)setBusy('')}
  }
  return <>{error&&<p role="alert" className="notice">{error}</p>}{packets.map(packet=><div className="reparse-packet" key={packet.packet_id}>
    <b>{packet.status==='ready'?'补证包已就绪':packet.status==='conflicts'?'发现解析冲突（X4）':packet.status==='no_new_information'?'未发现新增信息':'补证包暂不可用'}</b>
    <p>新增 {packet.new_facts_count} 项事实{packet.conflicts>0&&<> · {packet.conflicts} 项冲突，需要专业核对</>}</p>
    {packet.proof&&<small>原 PDF 第 {packet.proof.pages.join('、')} 页</small>}
    <small className="evidence-id">{packet.packet_id}</small>
    {packet.status==='ready'&&packet.new_facts_count>0&&<Button size="sm" disabled={disabled||!!busy} onClick={()=>previewPacket(packet)}>{busy==='preview:'+packet.packet_id?'正在加载事实…':previews[packet.packet_id]?'刷新事实预览':'预览新增事实'}</Button>}
    {previews[packet.packet_id]&&<section className="reparse-fact-preview" aria-label="新增补证事实预览"><p className="notice">待应用的补证事实；尚未进入原任务，也不是核查结论。数值由原 PDF 确定性解析并重新核验。</p>
      {previews[packet.packet_id].facts.map(fact=><article key={fact.fact_id} className="reparse-preview-fact"><h4>{fact.period} · {fact.title}</h4><strong>{fact.display_value}</strong><p>{fact.company} · {fact.scope} · {fact.nr} · {fact.version}</p><small>{fact.source_name} · 披露日期 {fact.publish_date}</small>
        {fact.evidence.map(id=><button key={id} className="evidence-link reparse-preview-evidence" onClick={()=>{setError('');setTarget({packet_id:packet.packet_id,evidence_id:id})}}>{id}</button>)}
      </article>)}
      {target?.packet_id===packet.packet_id&&<div className="reparse-preview-page"><small className="evidence-id">{target.evidence_id}</small>{imageUrl?<img className="evidence-image" alt="补证新增事实的原始 PDF 页面与定位" src={imageUrl}/>:<p className="muted">正在加载原始证据页面…</p>}</div>}
    </section>}
    {packet.status==='ready'&&packet.new_facts_count>0&&<Button size="sm" variant="primary" disabled={disabled||!!busy||!onNewRun} onClick={()=>applyPacket(packet)}>创建补证版本</Button>}
  </div>)}</>
}
