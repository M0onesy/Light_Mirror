import {useState} from 'react'
import {Button} from '../vendor/harness/packages/client/ui-primitives/src/Button'
import {Modal} from '../vendor/harness/packages/client/ui-primitives/src/Modal'
import type {Config,ConfigEnvelope,Json,Model,Provider,Route} from './types'
import {api,download,setLocalBearer} from './api'
import {copy} from './i18n'

const presets={deepseek:{vendor:'deepseek',protocol:'deepseek',base_url:'https://api.deepseek.com',model:'deepseek-flash',display_name:'DeepSeek'},kimi:{vendor:'moonshotai',protocol:'openai-completions',base_url:'https://api.moonshot.cn/v1',model:'kimi-k2.5',display_name:'Kimi'},openai:{vendor:'openai',protocol:'openai-responses',base_url:'https://api.openai.com/v1',model:'',display_name:'OpenAI GPT'},custom:{vendor:'custom',protocol:'openai-completions',base_url:'',model:'',display_name:'自定义服务'}}
type Preset=keyof typeof presets
type Section='workspace'|'harness'|'logging'
const protocols=[['deepseek','DeepSeek 原生'],['openai-completions','OpenAI Chat Completions / 兼容 API'],['openai-responses','OpenAI Responses'],['anthropic-messages','Anthropic Messages']]
const budgetFields=[['max_model_calls','模型请求上限'],['max_tool_calls','工具调用上限'],['max_total_tokens','Token 总预算'],['max_runtime_s','任务时限（秒）']]
const defaultFollowupBudget:Record<string,number>={max_model_calls:12,max_tool_calls:80,max_total_tokens:150000,max_runtime_s:180}
const pathFields:[string,string,'file'|'dir'][]=[['data_root','工作区','dir'],['draft_path','默认研报草稿（可选）','file'],['output_root','输出与历史','dir'],['corpus_root','财报语料','dir'],['knowledge_root','会计知识库','dir'],['facts_path','事实索引（可选）','file'],['temp_root','临时目录','dir']]
function newModel(id=''):Model{return {id,context_window:65536,max_tokens:4096,input:['text'],reasoning:'auto',temperature:null,compat:{}}}
function parseCompat(value:string):Record<string,Json>{
  let parsed:unknown
  try{parsed=JSON.parse(value)}catch{throw Error('高级兼容参数必须是有效的 JSON 对象')}
  if(!parsed||Array.isArray(parsed)||typeof parsed!=='object')throw Error('高级兼容参数必须是 JSON 对象')
  return parsed as Record<string,Json>
}
function unwrap(value:Config|ConfigEnvelope):Config{return 'config' in value?value.config:value}

export function Settings({config,onSaved,onClose}:{config:Config;onSaved:(config:Config)=>void;onClose:()=>void}) {
  const [tab,setTab]=useState('providers'),[draft,setDraft]=useState(()=>structuredClone(config)),[status,setStatus]=useState(''),[busy,setBusy]=useState(false)
  const [providerId,setProviderId]=useState(Object.keys(config.providers)[0]||''),[newId,setNewId]=useState(''),[secret,setSecret]=useState('')
  const empty:Provider={vendor:'custom',protocol:'openai-completions',base_url:'',display_name:'自定义服务',enabled:true,credential_ref:'',models:[newModel()],timeout_s:120,retries:2,material_access:true,verify_tls:true,chat_path:'/chat/completions'}
  const [provider,setProvider]=useState<Provider>(()=>structuredClone(config.providers[providerId]||empty)),[modelIndex,setModelIndex]=useState(0)
  const [compatText,setCompatText]=useState(()=>JSON.stringify(config.providers[providerId]?.models[0]?.compat||{},null,2))
  const [diagnostic,setDiagnostic]=useState(''),[discovered,setDiscovered]=useState<string[]>([]),[discoveredId,setDiscoveredId]=useState('')
  const [bearerSecret,setBearerSecret]=useState(''),[pathChecks,setPathChecks]=useState<Record<string,string>>({})
  const model=provider.models[modelIndex]||newModel()
  const routeDefault=draft.agent.routing.default
  const [makeDefault,setMakeDefault]=useState(!routeDefault?.provider||!routeDefault.model)

  async function save(patch:object,committed?:()=>void){
    setBusy(true)
    try{
      const saved=unwrap(await api<Config|ConfigEnvelope>('/api/config','PATCH',{...patch,revision:draft.revision}))
      // Authentication takes effect immediately. Update the in-memory token
      // before App reacts to its new config and makes another API request.
      committed?.();setDraft(saved);onSaved(saved)
      setStatus('设置已保存，正在运行的任务继续使用其冻结配置。');return saved
    }catch(error){setStatus((error as Error).message);return null}finally{setBusy(false)}
  }
  function edit(id:string){
    const value=structuredClone(draft.providers[id]||empty)
    if(!value.models.length)value.models=[newModel()]
    setProviderId(id);setNewId('');setSecret('');setProvider(value);setModelIndex(0)
    setCompatText(JSON.stringify(value.models[0].compat||{},null,2));setDiscovered([]);setDiscoveredId('');setDiagnostic('')
    setMakeDefault(!draft.agent.routing.default?.provider)
  }
  function preset(kind:Preset){
    const value=presets[kind]
    setProviderId('');setNewId(kind==='custom'?'':kind);setSecret('');setModelIndex(0)
    setProvider({...empty,vendor:value.vendor,protocol:value.protocol,base_url:value.base_url,display_name:value.display_name,models:[newModel(value.model)]})
    setCompatText('{}');setDiscovered([]);setDiscoveredId('');setDiagnostic('');setMakeDefault(!draft.agent.routing.default?.provider)
  }
  function updateModel(patch:Partial<Model>){setProvider({...provider,models:provider.models.map((item,index)=>index===modelIndex?{...item,...patch}:item)})}
  function selectModel(index:number){
    try{
      const models=provider.models.map((item,i)=>i===modelIndex?{...item,compat:parseCompat(compatText)}:item)
      setProvider({...provider,models});setModelIndex(index);setCompatText(JSON.stringify(models[index]?.compat||{},null,2))
    }catch(error){setStatus((error as Error).message)}
  }
  function addModel(){
    try{const models=provider.models.map((item,index)=>index===modelIndex?{...item,compat:parseCompat(compatText)}:item);models.push(newModel());setProvider({...provider,models});setModelIndex(models.length-1);setCompatText('{}')}
    catch(error){setStatus((error as Error).message)}
  }
  async function saveProvider(){
    const id=providerId||newId
    try{
      if(!/^[a-z][a-z0-9-]{0,63}$/.test(id))throw Error('供应商 ID 需为小写字母、数字和短横线')
      if(!providerId&&draft.providers[id])throw Error('此供应商 ID 已存在，请从供应商列表选择并编辑')
      const models=provider.models.map((item,index)=>({...newModel(),...item,id:item.id.trim(),...(index===modelIndex?{compat:parseCompat(compatText)}:{})}))
      if(!models.length||models.some(item=>!item.id)||new Set(models.map(item=>item.id)).size!==models.length)throw Error('请填写不重复的、供应商实际支持的模型 ID')
      const ref=provider.credential_ref||'MJ_KEY_'+id.toUpperCase().replaceAll('-','_')
      const value={...provider,models,credential_ref:ref,verify_tls:true}
      const agent=makeDefault?{...draft.agent,enabled:true,routing:{...draft.agent.routing,default:{provider:id,model:models[modelIndex]?.id||models[0].id}}}:undefined
      const saved=await save({providers:{[id]:value},...(agent?{agent}:{}),...(secret?{secrets:{[ref]:secret}}:{})})
      if(saved){setProviderId(id);setProvider(saved.providers[id]);setSecret('');setCompatText(JSON.stringify(saved.providers[id].models[modelIndex]?.compat||{},null,2))}
    }catch(error){setStatus((error as Error).message)}
  }
  async function probe(action:string){
    if(!providerId){setStatus('先保存供应商，再测试已保存配置');return}
    if(secret||JSON.stringify(provider)!==JSON.stringify(draft.providers[providerId])){setStatus('请先保存当前供应商、模型和密钥，再测试');return}
    setBusy(true);setDiagnostic('正在检测已保存配置…')
    try{
      const value=await api<{ok?:boolean;models?:{id:string}[];[key:string]:unknown}>(`/api/providers/${encodeURIComponent(providerId)}/${action}`,'POST',{model:model.id})
      if(action==='discover'){const ids=[...new Set((value.models||[]).map(item=>item.id))];setDiscovered(ids);setDiscoveredId(ids[0]||'')}
      setDiagnostic(JSON.stringify(value,null,2));setStatus(value.ok?'检测通过；模型发现结果仅声明模型可用，Agent 能力须单独测试。':'检测未通过，请查看实际原因。')
    }catch(error){setDiagnostic((error as Error).message)}finally{setBusy(false)}
  }
  function useDiscovered(){
    if(!discoveredId)return
    const index=provider.models.findIndex(item=>item.id===discoveredId)
    if(index>=0){selectModel(index);return}
    try{const models=provider.models.map((item,i)=>i===modelIndex?{...item,compat:parseCompat(compatText)}:item);if(!model.id.trim())models[modelIndex]=newModel(discoveredId);else models.push(newModel(discoveredId));setProvider({...provider,models});setModelIndex(models.findIndex(item=>item.id===discoveredId));setCompatText('{}');setStatus('已选择发现的模型，点击“保存供应商与密钥”后生效。')}
    catch(error){setStatus((error as Error).message)}
  }
  const modelRoutes=Object.entries(draft.providers).filter(([,item])=>item.enabled&&item.material_access).flatMap(([id,item])=>item.models.map(m=>({value:JSON.stringify({provider:id,model:m.id}),name:`${item.display_name||id} / ${m.id}`})))
  function setRoute(role:string,value:string){setDraft({...draft,agent:{...draft.agent,routing:{...draft.agent.routing,[role]:value?JSON.parse(value) as Route:role==='default'?{provider:'',model:''}:null}}})}
  function setPath(section:Section,key:string,value:string){setDraft({...draft,[section]:{...draft[section],[key]:value}})}
  async function pickPath(section:Section,key:string,kind:'file'|'dir'){
    setBusy(true)
    try{const result=await api<{path?:string;cancelled?:boolean}>('/api/paths/pick','POST',{kind});if(result.path)setPath(section,key,result.path)}catch(error){setStatus((error as Error).message)}finally{setBusy(false)}
  }
  async function testPath(section:Section,key:string){
    const value=String(draft[section][key]||'');if(!value){setPathChecks({...pathChecks,[`${section}.${key}`]:'请先填写或选择路径'});return}
    setBusy(true)
    try{const result=await api<{ok:boolean;is_file?:boolean;is_dir?:boolean;readable?:boolean;writable?:boolean}>('/api/config/test-path','POST',{path:value});setPathChecks({...pathChecks,[`${section}.${key}`]:`${result.ok?(result.is_dir?'目录存在':result.is_file?'文件存在':'路径存在'):'路径尚不存在'}；${result.readable?'可读':'不可读'}；${result.writable?'可写':'不可写'}`})}
    catch(error){setPathChecks({...pathChecks,[`${section}.${key}`]:(error as Error).message})}finally{setBusy(false)}
  }
  function pathField(section:Section,key:string,title:string,kind:'file'|'dir'){
    return <label key={`${section}.${key}`}>{title}<input aria-label={title} value={String(draft[section][key]||'')} onChange={event=>setPath(section,key,event.target.value)}/><span style={{display:'flex',gap:6}}><Button disabled={busy} onClick={()=>pickPath(section,key,kind)}>选择{kind==='file'?'文件':'目录'}</Button><Button disabled={busy} onClick={()=>testPath(section,key)}>检查路径</Button></span>{pathChecks[`${section}.${key}`]&&<small>{pathChecks[`${section}.${key}`]}</small>}</label>
  }
  async function saveService(){
    if(draft.server.bearer_token_required&&!config.server.bearer_token_required&&!bearerSecret.trim()){setStatus('首次开启本地访问验证时，请填写一个访问 Token，保存后本浏览器自动使用它。');return}
    const ref=draft.server.bearer_token_ref||'server_bearer'
    const harness={...draft.harness,enabled:true,profile:'mingjing',patch_file:'',disable_network:true,disable_shell:true,disable_code_mode:true,disable_telemetry:true}
    const saved=await save({harness,server:{...draft.server,bearer_token_ref:ref},security:draft.security,logging:{...draft.logging,redact_secrets:true},...(bearerSecret?{secrets:{[ref]:bearerSecret}}:{})},()=>{if(bearerSecret)setLocalBearer(bearerSecret);if(!draft.server.bearer_token_required)setLocalBearer('')})
    if(saved)setBearerSecret('')
  }
  async function importConfig(file:File){
    setBusy(true)
    try{
      const value=JSON.parse(await file.text()) as Config|ConfigEnvelope;const candidate=unwrap(value)
      if(candidate.server?.bearer_token_required&&!draft.server.bearer_token_required)throw Error('请先在服务设置中填写本地访问 Token 并开启验证，再导入要求验证的配置。')
      // Config exports carry references only. Imported access references cannot
      // replace the key that is currently authorizing this browser session.
      if(candidate.server?.bearer_token_required)candidate.server.bearer_token_ref=draft.server.bearer_token_ref||'server_bearer'
      const saved=unwrap(await api<Config|ConfigEnvelope>('/api/config/import','POST',{config:candidate,revision:draft.revision}))
      if(!saved.server.bearer_token_required)setLocalBearer('');setDraft(saved);onSaved(saved);setStatus('配置已导入，密钥继续使用当前用户存储。')
    }catch{setStatus('导入失败。请检查 JSON 配置版本、模型路由及本地访问验证设置。')}finally{setBusy(false)}
  }
  return <Modal open title={copy.settings} closeLabel={copy.close} onClose={onClose} contentClassName="settings-content" className="settings-modal">
    <div className="settings-tabs">{[['providers','模型与密钥'],['routing','角色与预算'],['paths','数据与路径'],['runtime','运行时'],['service','服务与安全']].map(([id,name])=><Button key={id} variant={tab===id?'primary':'ghost'} onClick={()=>{setTab(id);setDiagnostic('')}}>{name}</Button>)}</div>
    {tab==='providers'&&<>
      <div className="filter-row"><select aria-label="选择供应商" value={providerId} onChange={event=>edit(event.target.value)}><option value="">新增供应商</option>{Object.entries(draft.providers).map(([id,item])=><option key={id} value={id}>{item.display_name||id}</option>)}</select><select aria-label="供应商预设" value="" onChange={event=>preset(event.target.value as Preset)}><option value="" disabled>添加供应商…</option>{Object.entries(presets).map(([id,item])=><option key={id} value={id}>{item.display_name}</option>)}</select></div>
      <div className="form-grid">
        <label>供应商 ID<input aria-label="供应商 ID" disabled={Boolean(providerId)} value={providerId||newId} onChange={event=>setNewId(event.target.value)}/></label>
        <label>显示名称<input value={provider.display_name} onChange={event=>setProvider({...provider,display_name:event.target.value})}/></label>
        <label>协议<select aria-label="API 协议" value={provider.protocol} onChange={event=>setProvider({...provider,protocol:event.target.value})}>{protocols.map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>
        <label>API 地址<input aria-label="API 地址" value={provider.base_url} onChange={event=>setProvider({...provider,base_url:event.target.value})}/></label>
        <label>API Token<input type="password" autoComplete="new-password" aria-label="API Token" value={secret} onChange={event=>setSecret(event.target.value)} placeholder="留空保留当前加密密钥"/></label>
        <label>已配置模型<select aria-label="已配置模型" value={modelIndex} onChange={event=>selectModel(Number(event.target.value))}>{provider.models.map((item,index)=><option key={index} value={index}>{item.id||'新模型'}</option>)}</select></label>
        <label>模型 ID<input aria-label="模型 ID" value={model.id} onChange={event=>updateModel({id:event.target.value})} placeholder="填写供应商实际提供的模型名称"/></label>
        <label>单次输出 Token 上限<input aria-label="单次输出 Token 上限" type="number" min="1" value={model.max_tokens||4096} onChange={event=>updateModel({max_tokens:Number(event.target.value)})}/></label>
        <label>思考模式<select aria-label="思考模式" value={model.reasoning||'auto'} onChange={event=>updateModel({reasoning:event.target.value})}>{[['auto','自动（由适配器处理）'],['off','关闭'],['low','低'],['medium','中'],['high','高'],['max','最大'],['xhigh','极高']].map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>
        <label>请求超时（秒）<input type="number" min="1" max="1800" value={provider.timeout_s} onChange={event=>setProvider({...provider,timeout_s:Number(event.target.value)})}/></label>
      </div>
      <div className="form-actions"><Button disabled={busy} onClick={addModel}>添加模型</Button><Button disabled={busy||provider.models.length<2} onClick={()=>{const models=provider.models.filter((_,index)=>index!==modelIndex);setProvider({...provider,models});setModelIndex(0);setCompatText(JSON.stringify(models[0]?.compat||{},null,2))}}>移除当前模型</Button></div>
      <label><input type="checkbox" checked={makeDefault} onChange={event=>setMakeDefault(event.target.checked)}/>将当前模型设为默认 Agent 模型</label>
      <details><summary>高级连接与兼容参数</summary><div className="form-grid"><label>供应商适配标识<input value={provider.vendor} onChange={event=>setProvider({...provider,vendor:event.target.value})}/></label><label>Chat Completions 路径<input value={provider.chat_path||'/chat/completions'} onChange={event=>setProvider({...provider,chat_path:event.target.value})}/></label><label>模型上下文窗口<input type="number" min="1" value={model.context_window||65536} onChange={event=>updateModel({context_window:Number(event.target.value)})}/></label><label>温度（留空使用供应商默认）<input type="number" min="0" max="2" step="0.1" value={model.temperature??''} onChange={event=>updateModel({temperature:event.target.value===''?null:Number(event.target.value)})}/></label><label>失败重试次数<input type="number" min="0" max="5" value={provider.retries} onChange={event=>setProvider({...provider,retries:Number(event.target.value)})}/></label></div><label><input type="checkbox" checked={provider.enabled} onChange={event=>setProvider({...provider,enabled:event.target.checked})}/>启用供应商</label><label><input type="checkbox" checked={provider.material_access} onChange={event=>setProvider({...provider,material_access:event.target.checked})}/>允许向此供应商发送当前任务材料</label><label><input type="checkbox" checked disabled/>验证 TLS 证书（始终开启）</label><label>当前模型的高级兼容 JSON<textarea aria-label="高级兼容 JSON" rows={5} value={compatText} onChange={event=>setCompatText(event.target.value)} spellCheck={false}/></label><p className="muted">兼容参数只填写当前协议支持的选项，不填写密钥。密钥由 Windows DPAPI 单独保存。本地 HTTP 接口无需关闭 TLS 验证；外部 HTTPS 接口始终校验证书。</p></details>
      <div className="form-actions"><Button variant="primary" disabled={busy} onClick={saveProvider}>保存供应商与密钥</Button><Button disabled={busy||!providerId} onClick={()=>probe('discover')}>发现模型</Button><Button disabled={busy||!providerId} onClick={()=>probe('test')}>连接测试</Button><Button disabled={busy||!providerId} onClick={()=>probe('test-agent')}>Agent 工具闭环测试</Button></div>
      {discovered.length>0&&<div className="filter-row"><label>发现的模型<select aria-label="发现的模型" value={discoveredId} onChange={event=>setDiscoveredId(event.target.value)}>{discovered.map(id=><option key={id}>{id}</option>)}</select></label><Button disabled={busy} onClick={useDiscovered}>使用所选模型</Button></div>}
      <p className="muted">检测使用已保存配置。“Agent 工具闭环测试”会执行真实只读工具往返，普通模型发现不代表 Agent 能力通过。</p>{diagnostic&&<pre className="diagnostic">{diagnostic}</pre>}
    </>}
    {tab==='routing'&&<>
      <label><input type="checkbox" checked={draft.agent.enabled} onChange={event=>setDraft({...draft,agent:{...draft.agent,enabled:event.target.checked}})}/>启用原生金融 Agent</label>
      <div className="form-grid">{[['default','默认模型'],['planner','planner：独立绑定 A'],['verifier','verifier：独立绑定 B'],['reporter','reporter：报告组织']].map(([role,title])=><label key={role}>{title}<select aria-label={title} value={draft.agent.routing[role]?.provider?JSON.stringify(draft.agent.routing[role]):''} onChange={event=>setRoute(role,event.target.value)}><option value="">{role==='default'?'选择默认模型':'继承默认模型'}</option>{modelRoutes.map(route=><option key={route.value} value={route.value}>{route.name}</option>)}</select></label>)}</div>
      <h3>核查／分析预算</h3><div className="form-grid">{[...budgetFields,['max_subagents','同时活动子 Agent（最多 2）'],['max_depth','子 Agent 深度（最多 1）']].map(([key,title])=><label key={key}>{title}<input aria-label={`任务${title}`} type="number" min="1" max={key==='max_subagents'?2:key==='max_depth'?1:undefined} value={draft.agent.budget[key]} onChange={event=>setDraft({...draft,agent:{...draft.agent,budget:{...draft.agent.budget,[key]:Number(event.target.value)}}})}/></label>)}</div>
      <h3>材料追问预算</h3><div className="form-grid">{budgetFields.map(([key,title])=><label key={key}>{title}<input aria-label={`追问${title}`} type="number" min="1" value={draft.agent.followup_budget?.[key]??defaultFollowupBudget[key]} onChange={event=>setDraft({...draft,agent:{...draft.agent,followup_budget:{...(draft.agent.followup_budget||{}),[key]:Number(event.target.value)}}})}/></label>)}</div>
      <p className="muted">当前每条追问最多 {(draft.agent.followup_budget?.max_total_tokens??defaultFollowupBudget.max_total_tokens).toLocaleString('en-US')} Token，可按需要调整；已保存的自定义预算保持原值。保存后影响新任务，当前任务会话继续使用其冻结配置。</p>
      <p className="muted">A 与 B 使用独立会话。预算耗尽时，未处理项仍显示在覆盖账本中。</p>
      <Button variant="primary" disabled={busy} onClick={()=>{if(draft.agent.enabled&&!draft.agent.routing.default?.model){setStatus('请先选择默认模型');return}void save({agent:draft.agent})}}>保存角色与预算</Button>
    </>}
    {tab==='paths'&&<><div className="form-grid">{pathFields.map(([key,title,kind])=>pathField('workspace',key,title,kind))}</div><p className="muted">大型财报保留在外部目录。草稿复制到任务工作区，旧结果保持冻结。路径检查使用已保存的访问权限。默认事实索引可由任务中选择的索引覆盖；临时目录用于 Agent 与消息重放。</p><p className="muted">会计知识库在新任务启动时建立只读索引并冻结哈希。Agent 可以查阅准则、解释与案例原文；来源、版本和有效日期声明尚待独立核验，不直接替代签发规则或产生数字判定。日期未知或晚于研报日的原文不能作为当时已知依据。</p><div className="form-actions"><Button variant="primary" disabled={busy} onClick={()=>save({workspace:draft.workspace})}>保存路径</Button><Button disabled={busy} onClick={async()=>{setBusy(true);try{setDiagnostic(JSON.stringify(await api('/api/config/validate','POST',{config:draft}),null,2))}catch(error){setStatus((error as Error).message)}finally{setBusy(false)}}}>校验配置</Button></div>{diagnostic&&<pre>{diagnostic}</pre>}</>}
    {tab==='runtime'&&<>
      <p>默认使用内嵌金融 Harness。金融 profile 固定为 mingjing；关闭 Shell、任意代码执行、开放搜索和遥测。</p>
      <label>运行时<select aria-label="Harness 运行时" value={String(draft.harness.runtime_mode||'embedded')} onChange={event=>setDraft({...draft,harness:{...draft.harness,runtime_mode:event.target.value,enabled:true}})}><option value="embedded">内嵌金融 Harness（默认）</option><option value="external">外部原生运行时（开发）</option></select></label>
      {draft.harness.runtime_mode==='external'&&<div className="form-grid">{pathField('harness','executable','外部原生运行时路径','file')}</div>}
      <label>运行时启动超时（秒）<input type="number" min="1" value={Number(draft.harness.timeout_s||120)} onChange={event=>setDraft({...draft,harness:{...draft.harness,timeout_s:Number(event.target.value)}})}/></label><p className="muted">此项控制原生运行时启动握手；核查与追问总时限在“角色与预算”中设置。</p>
      <div className="form-actions"><Button variant="primary" disabled={busy} onClick={()=>save({harness:{...draft.harness,enabled:true,profile:'mingjing',patch_file:'',disable_network:true,disable_shell:true,disable_code_mode:true,disable_telemetry:true}})}>保存运行时</Button><Button disabled={busy} onClick={async()=>{setBusy(true);try{setDiagnostic(JSON.stringify(await api('/api/config/test-harness','POST',{}),null,2))}catch(error){setStatus((error as Error).message)}finally{setBusy(false)}}}>检测已保存的运行时</Button></div>{diagnostic&&<pre>{diagnostic}</pre>}
    </>}
    {tab==='service'&&<>
      <h3>本地服务</h3><div className="form-grid"><label>监听地址<select value={draft.server.host} onChange={event=>setDraft({...draft,server:{...draft.server,host:event.target.value}})}>{['127.0.0.1','localhost','::1'].map(host=><option key={host}>{host}</option>)}</select></label><label>端口（0 自动选择）<input type="number" min="0" max="65535" value={draft.server.port} onChange={event=>setDraft({...draft,server:{...draft.server,port:Number(event.target.value)}})}/></label><label>本地访问 Token<input aria-label="本地访问 Token" type="password" autoComplete="new-password" value={bearerSecret} onChange={event=>setBearerSecret(event.target.value)} placeholder="留空保留现有访问密钥"/></label></div>
      <label><input type="checkbox" checked={draft.server.auto_open_browser} onChange={event=>setDraft({...draft,server:{...draft.server,auto_open_browser:event.target.checked}})}/>启动时自动打开浏览器</label>
      <label><input aria-label="要求本地访问 Token" type="checkbox" checked={Boolean(draft.server.bearer_token_required)} onChange={event=>setDraft({...draft,server:{...draft.server,bearer_token_required:event.target.checked}})}/>要求本地访问 Token</label>
      <p className="muted">Token 加密保存；保存后本浏览器会立即使用新 Token。换浏览器或重启后在登录界面输入它。监听地址与端口在重启后生效。</p>
      <h3>路径访问</h3><label><input type="checkbox" checked={Boolean(draft.security.allow_external_paths)} onChange={event=>setDraft({...draft,security:{...draft.security,allow_external_paths:event.target.checked}})}/>允许选择工作区之外的材料</label>
      <label>允许的根目录（每行一项）<textarea aria-label="允许的根目录" rows={3} value={Array.isArray(draft.security.allowed_roots)?draft.security.allowed_roots.join('\n'):''} onChange={event=>setDraft({...draft,security:{...draft.security,allowed_roots:event.target.value.split(/\r?\n/).map(item=>item.trim()).filter(Boolean)}})}/></label>
      <Button disabled={busy} onClick={async()=>{setBusy(true);try{const result=await api<{path?:string}>('/api/paths/pick','POST',{kind:'dir'});if(result.path)setDraft({...draft,security:{...draft.security,allowed_roots:[...new Set([...(Array.isArray(draft.security.allowed_roots)?draft.security.allowed_roots:[]),result.path])]}})}catch(error){setStatus((error as Error).message)}finally{setBusy(false)}}}>选择并添加根目录</Button>
      <label>上传大小上限（MB）<input type="number" min="1" value={Number(draft.security.max_upload_mb||100)} onChange={event=>setDraft({...draft,security:{...draft.security,max_upload_mb:Number(event.target.value)}})}/></label>
      <h3>日志</h3><div className="form-grid">{pathField('logging','directory','日志目录','dir')}<label>日志级别<select value={String(draft.logging.level||'INFO')} onChange={event=>setDraft({...draft,logging:{...draft.logging,level:event.target.value}})}>{['DEBUG','INFO','WARNING','ERROR'].map(level=><option key={level}>{level}</option>)}</select></label><label>保留运行数量（策略预留）<input type="number" disabled min="1" value={Number(draft.logging.retain_runs||30)}/></label><label>保留天数（策略预留）<input type="number" disabled min="1" value={Number(draft.logging.retain_days||90)}/></label></div><p className="muted">日志目录实际记录脱敏应用事件；每个任务的审计日志保存在输出目录。当前开发版本保留历史，暂不自动按天数或数量清理。</p>
      <div className="form-actions"><Button variant="primary" disabled={busy} onClick={saveService}>保存服务、安全与日志</Button><Button disabled={busy} onClick={async()=>{try{download('MingJing-config.json',await api('/api/config/export'))}catch(error){setStatus((error as Error).message)}}}>导出配置</Button></div>
      <label>导入配置<input type="file" accept=".json" disabled={busy} onChange={event=>{const file=event.target.files?.[0];if(file)void importConfig(file);event.target.value=''}}/></label><p className="muted">配置导出不包含密钥；现有任务不受配置更新影响。</p>
    </>}
    {status&&<div role="status" className="notice">{status}</div>}
  </Modal>
}
