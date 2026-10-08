import {useEffect,useState} from 'react'
import {Button} from '../vendor/harness/packages/client/ui-primitives/src/Button'
import {api} from './api'
import type {Run} from './types'
import {ReparsePackets} from './ReparsePackets'
import type {ReparsePacket} from './ReparsePackets'

export function ReparseProposals({runId,onNewRun}:{runId:string;onNewRun:(run:Run)=>void}) {
  const [packets,setPackets]=useState<ReparsePacket[]>([]),[error,setError]=useState(''),[loading,setLoading]=useState(true),[refresh,setRefresh]=useState(0)
  useEffect(()=>{
    const controller=new AbortController()
    setLoading(true);setError('');setPackets([])
    api<{packets:ReparsePacket[]}>(`/api/runs/${encodeURIComponent(runId)}/reparse-packets`,'GET',undefined,controller.signal)
      .then(result=>{if(!controller.signal.aborted)setPackets(result.packets)})
      .catch(error=>{if(!controller.signal.aborted)setError((error as Error).message)})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false)})
    return()=>controller.abort()
  },[runId,refresh])
  return <section className="reparse-proposals" aria-label="任务补证包"><details open={packets.length>0}>
    <summary>可用页面补证{packets.length>0?`（${packets.length} 个包）`:''}</summary>
    <p className="muted">包括 Agent 局部追检和人工页面补证。补证材料独立保存，应用后创建新任务，原结论保持冻结。</p>
    <Button size="sm" disabled={loading} onClick={()=>setRefresh(value=>value+1)}>{loading?'正在读取补证包…':'刷新补证包'}</Button>
    {error?<p role="alert" className="notice">补证包暂不可读取：{error}</p>:!loading&&!packets.length?<p className="muted">本任务尚无登记的页面补证包。可在原 PDF 证据面板局部重解析，或补充可读财报材料。</p>:null}
    <ReparsePackets key={runId} runId={runId} packets={packets} onNewRun={onNewRun} disabled={loading}/>
  </details></section>
}
