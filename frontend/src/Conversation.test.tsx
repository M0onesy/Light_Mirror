// @vitest-environment jsdom
import React,{useState} from 'react'
import {afterEach,describe,expect,it,vi} from 'vitest'
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {Conversation} from './Conversation'
import {api} from './api'
import type {Session} from './types'

vi.mock('./api',()=>({api:vi.fn()}))
vi.mock('../vendor/harness/packages/client/ui-primitives/src/Button',()=>({Button:({children,onClick,disabled}:any)=><button onClick={onClick} disabled={disabled}>{children}</button>}))
vi.mock('../vendor/harness/packages/client/ui-primitives/src/markdown/MarkdownText',()=>({MarkdownText:({text}:any)=><div>{text}</div>}))

const frozen:Session={session_id:'session-012345678901234567890123',run_id:'run',status:'ready',messages:[
  {role:'assistant',text:'冻结的金融工具回答。',time:1,message_id:'message-0001',agent_message_manifest_sha256:'a'.repeat(64)}]}

afterEach(()=>{cleanup();vi.resetAllMocks()})

describe('frozen message replay controls',()=>{
  it('reports a newly created session without remounting or replacing the subsequent answer',async()=>{
    const created={...frozen,messages:[]},changed=vi.fn()
    vi.mocked(api).mockImplementation(async(url)=>url==='/api/sessions'?created:frozen)
    function Host(){
      const [id,setId]=useState<string>()
      return <Conversation runId="run" initialId={id} onError={vi.fn()} onNewRun={vi.fn()} onSelect={vi.fn()}
        onSessionChanged={state=>{changed(state);setId(state.session_id)}}/>
    }
    render(<Host/>)
    fireEvent.change(screen.getByLabelText('金融问题'),{target:{value:'查看已冻结的核查结果'}})
    fireEvent.click(screen.getByText('发送问题 ↑'))
    await screen.findByText('冻结的金融工具回答。')
    expect(changed).toHaveBeenCalledWith(created)
    expect(changed).toHaveBeenCalledWith(frozen)
    expect(api).toHaveBeenCalledTimes(2)
    expect(api).toHaveBeenCalledWith(`/api/sessions/${frozen.session_id}/messages`,'POST',{question:'查看已冻结的核查结果'})
  })

  it('refuses to bind another material task session to the current conversation',async()=>{
    vi.mocked(api).mockResolvedValue({...frozen,run_id:'other-run'})
    const onError=vi.fn(),onSessionChanged=vi.fn()
    render(<Conversation runId="run" initialId={frozen.session_id} onError={onError} onNewRun={vi.fn()} onSelect={vi.fn()} onSessionChanged={onSessionChanged}/>)
    await waitFor(()=>expect(onError).toHaveBeenCalledWith('会话与当前材料任务不匹配，已拒绝载入。'))
    expect(onSessionChanged).not.toHaveBeenCalled()
    expect(screen.queryByText('冻结的金融工具回答。')).toBeNull()
  })

  it('requests the registered message with no candidate values or live-model options',async()=>{
    vi.mocked(api).mockImplementation(async(url,method)=>method==='GET'?frozen:
      {status:'passed',real_harness_loop:true,network_calls:0})
    render(<Conversation runId="run" initialId={frozen.session_id} onError={vi.fn()} onNewRun={vi.fn()} onSelect={vi.fn()}/>)
    fireEvent.click(await screen.findByText('重放这条回答'))
    await screen.findByText(/离线重放通过/)
    expect(api).toHaveBeenCalledWith(`/api/sessions/${frozen.session_id}/messages/message-0001/replay`,'POST',{})
    expect(screen.getByText('冻结的金融工具回答。')).toBeTruthy()
  })

  it('never presents incomplete or live fallback as an offline success',async()=>{
    vi.mocked(api).mockImplementation(async(_url,method)=>method==='GET'?frozen:
      {status:'failed',real_harness_loop:false,network_calls:0,reason:'REPLAY_CACHE_MISS'})
    render(<Conversation runId="run" initialId={frozen.session_id} onError={vi.fn()} onNewRun={vi.fn()} onSelect={vi.fn()}/>)
    fireEvent.click(await screen.findByText('重放这条回答'))
    await screen.findByText(/重放未通过：REPLAY_CACHE_MISS/)
    expect(screen.queryByText(/离线重放通过/)).toBeNull()
  })

  it('keeps historical messages without replay caches readable without a replay promise',async()=>{
    const historical={...frozen,messages:[{...frozen.messages[0],agent_message_manifest_sha256:undefined}]}
    vi.mocked(api).mockResolvedValue(historical)
    render(<Conversation runId="run" initialId={frozen.session_id} onError={vi.fn()} onNewRun={vi.fn()} onSelect={vi.fn()}/>)
    await screen.findByText('冻结的金融工具回答。')
    expect(screen.queryByText('重放这条回答')).toBeNull()
  })

  it('opens a logical knowledge quotation without inventing a PDF page',async()=>{
    const knowledge={...frozen,messages:[{...frozen.messages[0],source_references:[{
      evidence_id:'kb:text:original',page:null,name:'会计准则.md',
      reference_status:'unverified_source_quotation',material_kind:'accounting_knowledge',
    }]}]}
    vi.mocked(api).mockResolvedValue(knowledge)
    const onSelect=vi.fn()
    render(<Conversation runId="run" initialId={frozen.session_id} onError={vi.fn()} onNewRun={vi.fn()} onSelect={onSelect}/>)
    fireEvent.click(await screen.findByText('查看原文证据 · 文本片段'))
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({
      evidence:['kb:text:original'],reason:expect.stringContaining('尚未独立核验'),
      evidence_context:{session_id:frozen.session_id,message_id:'message-0001'},
    }))
    expect(screen.queryByText(/第 null 页/)).toBeNull()
  })
})
