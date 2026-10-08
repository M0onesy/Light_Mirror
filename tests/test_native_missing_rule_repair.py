"""Real upstream Agent Loop repairs 11b prerequisites; provider is local fixture."""
import copy
import json
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mingjing.agent_replay import logical_session_trace
from mingjing.financial_tools import tool_definitions
from mingjing.harness_client import HarnessRuntime, runtime_fingerprint
from mingjing.providers import ModelConfig, ProviderConfig, resolve_route

from test_native_runtime_safety import loop_carrier
from test_native_verification_requirements import owner_for


def test_native_model_observes_required_rule_repairs_then_abstains_and_replays(tmp_path,loop_carrier):
    online,claims = owner_for(tmp_path/'online')
    ids = [row.claim_id for row in claims]
    state = {'requests':0,'saw_requirement':False,'saw_abstention':False}

    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            serialized = json.dumps(body)
            step = state['requests']
            state['requests']+=1
            if step==0:
                tool,arguments = 'mj_get_facts',{'binding_ids':ids}
            elif step==1:
                tool,arguments = 'mj_verify_bindings',{'binding_ids':ids}
            elif step==2:
                state['saw_requirement'] = 'verification_reads_required' in serialized and 'E1-value' in serialized
                assert state['saw_requirement']
                tool,arguments = 'mj_get_rule',{'rule_id':'E1-value'}
            elif step==3:
                tool,arguments = 'mj_verify_bindings',{'binding_ids':ids}
            else:
                state['saw_abstention'] = 'abstained' in serialized and 'X1' in serialized
                assert state['saw_abstention']
                tool,arguments = None,None
            if tool:
                delta={'tool_calls':[{'index':0,'id':'fixture-'+str(step),'type':'function',
                    'function':{'name':tool,'arguments':json.dumps(arguments)}}]}
                finish='tool_calls'
            else:
                delta,finish={'content':'Completed registered missing-material verification.'},'stop'
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream')
            self.end_headers()
            for chunk in (
                {'id':'fixture','model':'fixture','choices':[{'index':0,'delta':delta,'finish_reason':None}]},
                {'id':'fixture','model':'fixture','choices':[{'index':0,'delta':{},'finish_reason':finish}],
                 'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}},
            ):
                self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()

    provider=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    thread=threading.Thread(target=provider.serve_forever,daemon=True)
    thread.start()
    config=copy.deepcopy(online.config)
    config['providers']={'fixture':asdict(ProviderConfig(protocol='deepseek',
        base_url=f'http://127.0.0.1:{provider.server_port}',credential_ref='fixture_key',
        models=[ModelConfig(id='fixture',reasoning='off')],timeout_s=10,retries=0))}
    config['agent']['routing']['default']={'provider':'fixture','model':'fixture'}
    online.config=config
    online.secret_resolver=lambda _: 'keyless-only'

    def run(owner,home):
        runtime=HarnessRuntime(home,owner._incoming,event_handler=owner._event,
                               expected_runtime_lock=runtime_fingerprint())
        runtime.start()
        try:
            names=['mj_get_facts','mj_get_rule','mj_verify_bindings']
            definitions=[row for row in tool_definitions('coordinator') if row['name'] in names]
            runtime.open_session(session_id='coordinator',role='coordinator',run_id=owner.run_id,
                resource_lock=owner.resource_lock,route=resolve_route(config),
                tool_definitions=definitions,allowed_tools=names,max_steps=6,replay_only=owner.replay_only)
            result=runtime.prompt('coordinator','Exercise missing facts, a safe prerequisite rejection and repair.',timeout_s=25)
            assert result['native_loop'] and result['finish_reason']=='completed',result
        finally:
            runtime.close()

    try:
        run(online,tmp_path/'online-runtime')
    finally:
        provider.shutdown()
        provider.server_close()
    assert state=={'requests':5,'saw_requirement':True,'saw_abstention':True}
    coordinator_trace=[row for row in online.dispatcher.trace if row['role']=='coordinator']
    assert [row['status'] for row in coordinator_trace]==['success','denied','success','success']
    assert coordinator_trace[-1]['output']['items'][0]['status']=='abstained'
    assert coordinator_trace[1]['output']['error']['code']=='verification_reads_required'
    assert set(coordinator_trace[1]['input'])=={'input_sha256'}
    assert online.native_request_count==5 and len(online.model_cache)==5
    offline,_=owner_for(tmp_path/'offline')
    offline.config=config
    offline.model_cache=copy.deepcopy(online.model_cache)
    offline.replay_only=True
    offline.secret_resolver=lambda _: (_ for _ in ()).throw(AssertionError('Replay must not resolve a credential'))
    run(offline,tmp_path/'offline-runtime')
    assert offline.replay_cache_misses==[] and offline.native_request_count==5
    # Independent binding setup was identical; coordinator failure and repair
    # are genuinely re-executed through the native loop without the provider.
    records=[{'session_id':role,'role':role,'mention_ids':ids} for role in ('planner','verifier','coordinator')]
    assert logical_session_trace(offline.dispatcher.trace,records)==logical_session_trace(online.dispatcher.trace,records)
    assert state['requests']==5
