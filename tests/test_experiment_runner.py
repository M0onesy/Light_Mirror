"""Actual reruns and benchmark isolation, using fictitious local material."""
from dataclasses import replace
from decimal import Decimal
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import experiment_runner as runner
from mingjing.config import ConfigManager
from mingjing.models import Fact, AtomicClaim
from mingjing.facts import FactStore
from mingjing.judge import Judge


@pytest.fixture
def material(tmp_path):
    corpus=tmp_path/'corpus'; corpus.mkdir()
    path=corpus/'2025-03-01_2024年年度报告.md'
    path.write_text('合并利润表\n单位：万元\n2024年|2023年\n营业收入 100 80\n',encoding='utf-8')
    row={'id':'one','set':'development','company':'公司甲','period':'2024Y','report_date':'2025-03-31',
         'draft_text':'公司甲2024年合并口径营业收入为1004000.00元。',
         'source_documents':[{'path':path.name,'sha256':runner.sha256_file(path)}]}
    cfg=ConfigManager(tmp_path/'app'/'config.json').to_dict()
    return corpus,row,cfg


def experiment(baseline='kernel',ablation='none'):
    return runner.validate_experiments([{'name':baseline+'-'+ablation,'baseline':baseline,'ablation':ablation}])[0]


def test_numeric_gold_never_enters_execution_input(material,tmp_path):
    corpus,row,_=material
    row.update(gold_label={'candidate_value':'999999','cells':[{'printed_value':'999999'}]},gold_status='violated')
    case=runner.execution_case(row,corpus,tmp_path/'input')
    assert 'gold_label' not in json.dumps(case) and '999999' not in json.dumps(case)
    assert set(case['request'])=={'draft','corpus','corpus_additions','company','period','report_date','scope_declaration','task_type'}


@pytest.mark.parametrize('field',['facts','observed_candidate','computed_context','binding_fixture_status'])
def test_supplied_prediction_shortcuts_are_rejected(material,tmp_path,field):
    corpus,row,_=material; row[field]=[]
    with pytest.raises(ValueError,match='supplied'):
        runner.execution_case(row,corpus,tmp_path/'input')


def test_unregistered_d3_cannot_be_consumed(material,tmp_path):
    corpus,row,_=material; row['set']='D3'
    with pytest.raises(ValueError,match='D3'):
        runner.execution_case(row,corpus,tmp_path/'input')
    assert not (tmp_path/'input').exists()


def test_source_tampering_and_path_escape_are_rejected(material,tmp_path):
    corpus,row,_=material
    row['source_documents'][0]['sha256']='0'*64
    with pytest.raises(ValueError,match='hash'):
        runner.execution_case(row,corpus,tmp_path/'input')
    external=tmp_path/'elsewhere.md'; external.write_text('original')
    row['source_documents'][0]={'path':'../elsewhere.md','sha256':runner.sha256_file(external)}
    with pytest.raises(ValueError):
        runner.execution_case(row,corpus,tmp_path/'input')


def test_policy_is_installed_before_execution_and_restored_after_failure():
    from mingjing import agent,financial_tools,judge
    original=agent.Judge,financial_tools.Judge,judge.fact_interval
    with pytest.raises(RuntimeError):
        with runner.judging_policy('point-tolerance') as cls:
            assert agent.Judge is cls and financial_tools.Judge is cls
            fact=Fact('f','甲','REVENUE','2024Y',value=Decimal('1000000'),display_unit='万元',decimals=0)
            assert judge.fact_interval(fact).lower==judge.fact_interval(fact).upper==fact.value
            raise RuntimeError('test interruption')
    assert (agent.Judge,financial_tools.Judge,judge.fact_interval)==original


def test_interval_vs_point_is_actual_fresh_material_reexecution(material,tmp_path):
    corpus,row,cfg=material
    case=runner.execution_case(row,corpus,tmp_path/'input')
    regular=runner.execute_case(case,experiment(),cfg,lambda _:None,tmp_path/'interval',perform_repairs=False)
    point=runner.execute_case(case,experiment(ablation='point-tolerance'),cfg,lambda _:None,tmp_path/'point',perform_repairs=False)
    assert regular['results'][0]['status']=='verified'
    assert point['results'][0]['status']=='violated'
    assert point['results'][0]['verification_basis']=='experimental_point_expected_with_claim_display_tolerance'
    assert regular['run_id']!=point['run_id'] and regular['prediction_sha256']!=point['prediction_sha256']
    assert regular['model_requests']==point['model_requests']==0


def test_cutoff_disabled_changes_fact_selection_before_verdict():
    claim=AtomicClaim('c','甲2024年营业收入100元',0,20,company='甲',account='REVENUE',period='2024Y',
                      candidate_value='100',unit='元',display_decimals=2)
    store=FactStore([Fact('old','甲','REVENUE','2024Y',value=Decimal(100),source_doc='old',publish_date='2025-03-01'),
                     Fact('future','甲','REVENUE','2024Y',value=Decimal(150),source_doc='future',publish_date='2025-04-15')])
    assert Judge(store,'2025-03-31').verify(claim).status=='verified'
    assert runner.CutoffDisabledJudge(store,'2025-03-31').verify(claim).status=='violated'


def test_rule_baseline_really_ignores_scope_and_version():
    claim=AtomicClaim('c','合并收入90',0,6,company='甲',account='REVENUE',period='2024Y',candidate_value='90')
    facts=[Fact('consolidated','甲','REVENUE','2024Y',value=Decimal(100)),
           Fact('parent','甲','REVENUE','2024Y',scope='母公司',value=Decimal(90),publish_date='2099-04-15')]
    agent=SimpleNamespace(claims=[claim],facts=facts)
    assert runner.exact_match_results(agent)[0]['status']=='verified'


def test_single_binding_does_not_invoke_binder_b(monkeypatch):
    calls=[]
    def original(self,role,group,instructions,prompt,stop_event=None):
        calls.append(role)
        self.dispatcher.bindings[role]['m']={'company':'甲','account':'REVENUE','period':'2024Y',
                                           'scope':'合并','relation':'value','base_period':''}
    monkeypatch.setattr(runner.FinancialAgent,'_bind_role_group',original)
    import threading
    events=[]
    agent=runner.SingleBindingAgent.__new__(runner.SingleBindingAgent)
    agent.dispatcher=SimpleNamespace(bindings={'planner':{},'verifier':{}},lock=threading.RLock())
    agent.audit=SimpleNamespace(emit=lambda *args,**kwargs:events.append(kwargs))
    agent.save_state=lambda _:None
    agent._bind_role_group('planner',['m'],'instructions','prompt')
    agent._bind_role_group('verifier',['m'],'instructions','prompt')
    assert calls==['planner'] and agent.dispatcher.bindings['verifier']==agent.dispatcher.bindings['planner']
    assert events[0]['second_binding_origin']=='copied_A_gate_bypass_not_independent_model'


def test_rag_prompt_is_original_text_only_and_direct_reads_all_blocks():
    blocks=[{'id':'one','report_id':'report-1','page':1,'text':'营业收入100.00元'},
            {'id':'two','report_id':'report-1','page':2,'text':'董事人员简历'},
            {'id':'draft','page':1,'text':'这不是年报'}]
    agent=SimpleNamespace(company='公司甲',text='2024年营业收入100元',request={'report_date':'2025-03-31'},
                          dispatcher=SimpleNamespace(blocks={b['id']:b for b in blocks}),
                          facts=[{'value':'fake-secret-fact-999999'}])
    direct,ids,_=runner.baseline_prompt(agent,'direct-llm')
    rag,rag_ids,_=runner.baseline_prompt(agent,'rag',1)
    assert ids=={'one','two'} and rag_ids=={'one'}
    assert '董事人员简历' in direct and '董事人员简历' not in rag
    assert 'fake-secret-fact-999999' not in direct+rag


@pytest.mark.parametrize('variant',['truncated','invented_evidence','invalid_span','no_abstention_code'])
def test_invalid_model_baseline_cannot_be_scored_as_success(variant):
    row={'source_span':{'start':0,'end':3},'status':'verified','evidence':['real']}
    response={'finish_reason':'stop','final_response':json.dumps({'results':[row]})}
    if variant=='truncated': response['finish_reason']='max-tokens'
    elif variant=='invented_evidence': row['evidence']=['fabricated']
    elif variant=='invalid_span': row['source_span']['end']=100
    else: row['status']='abstained'
    response['final_response']=json.dumps({'results':[row]})
    with pytest.raises(ValueError): runner.parse_model_predictions(response,'100.00',{'real'})


def test_fresh_repair_rerun_has_no_gold_input(material,tmp_path):
    corpus,row,cfg=material
    row['draft_text']='公司甲2024年合并口径营业收入为2000000.00元。'
    case=runner.execution_case(row,corpus,tmp_path/'input')
    record=runner.execute_case(case,experiment(),cfg,lambda _:None,tmp_path/'case')
    assert record['results'][0]['status']=='violated'
    # Coarse original disclosure corrections produce an interval, not a
    # fictitious fully precise replacement; a failed repair remains visible.
    assert record['repairs'][0]['attempted'] is True
    assert (tmp_path/'case'/'repairs'/'0'/'run'/'experiment_results.json').is_file()
    assert record['repairs'][0]['status']=='rechecked'


def test_physical_cell_score_does_not_count_a_large_page_quote():
    ref={'path':'source.pdf','sha256':'a'*64,'page':1,'bbox':[100,100,140,110],
         'table':'合并利润表','row':'营业收入','col':'2024年'}
    truth={'gold_label':{'cells':[{'evidence':[ref]}]}}
    broad={'document_sha256':'a'*64,'page':1,'bbox':[0,0,500,700],
           'table':'合并利润表','row':'营业收入','col':'2024年'}
    score=runner.physical_evidence_scores({'evidence_locations':{'e':broad}},truth)
    assert score['page']['rate']==score['table']['rate']==1 and score['cell']['rate']==0
    broad['bbox']=ref['bbox']
    assert runner.physical_evidence_scores({'evidence_locations':{'e':broad}},truth)['cell']['rate']==1


def test_failed_missing_and_multi_claims_stay_in_score_denominator():
    human={'gold_status':'verified','gold_label':{'cells':[]}}
    for status,results in [('failed',[]),('partial',[{'status':'verified'}]),
                           ('executed',[{'status':'verified'},{'status':'verified'}])]:
        score=runner.score_record({'case_id':'one','status':status,'results':results},human)
        assert score['strict_match'] is False


def test_development_run_is_actual_kernel_without_claiming_human_recall(material,tmp_path):
    corpus,row,cfg=material
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'schema_version':runner.SCHEMA,'cases':[row],
        'experiments':[{'name':'kernel','baseline':'kernel'},{'name':'exact','baseline':'rule-exact-match'}]}),encoding='utf-8')
    out=tmp_path/'measure'
    result=runner.run_experiments(manifest,corpus,out,cfg,lambda _:None)
    assert result['status_counts']['executed']==2
    assert result['full_report_mention_recall']['status']=='unknown'
    assert result['human_scoring']=='not_executed' and result['professional_acceptance'] is False
    with pytest.raises(ValueError,match='immutable'):
        runner.run_experiments(manifest,corpus,out,cfg,lambda _:None)


def test_incomplete_preregistration_cannot_consume_formal_matrix(material):
    _,_,cfg=material
    plan=runner.preregister_experiments([{'name':'only-kernel','baseline':'kernel'}],cfg)
    with pytest.raises(ValueError,match='full B1'):
        runner.validate_preregistered_plan(plan,cfg,lambda _:None)
    assert runner.validate_preregistered_plan(plan,cfg,lambda _:None,require_full_matrix=False)[0]['baseline']=='kernel'


@pytest.mark.parametrize('change',['hash','config','code'])
def test_sealed_experiment_matrix_rejects_drift_before_any_model_call(material,monkeypatch,change):
    _,_,cfg=material
    plan=runner.preregister_experiments([{'name':'only-kernel','baseline':'kernel'}],cfg)
    mutated=copy.deepcopy(cfg)
    if change=='hash': plan['experiments'][0]['repetitions']=3
    elif change=='config': mutated['revision']+=1
    else: monkeypatch.setattr(runner,'financial_fingerprint',lambda:{'changed':True})
    with pytest.raises(ValueError):
        runner.validate_preregistered_plan(plan,mutated,lambda _:None,require_full_matrix=False)


@pytest.mark.parametrize('finish',['stop','max-tokens'])
def test_direct_model_baseline_has_no_tools_and_always_closes_owned_session(material,tmp_path,monkeypatch,finish):
    _,_,cfg=material
    cfg=copy.deepcopy(cfg)
    cfg['providers']={'fixture':{'enabled':True,'material_access':True,'protocol':'openai-completions',
        'base_url':'http://127.0.0.1','credential_ref':'fixture_ref','models':[{'id':'fixture-model'}]}}
    cfg['agent']['routing']={'default':{'provider':'fixture','model':'fixture-model'}}
    events=[]
    class Runtime:
        def open_session(self,**spec):
            events.append(('open',spec))
            assert spec['tool_definitions']==spec['allowed_tools']==[]
        def prompt(self,sid,text,**options):
            events.append(('prompt',sid))
            return {'finish_reason':finish,'final_response':json.dumps({'results':[
                {'source_span':{'start':0,'end':3},'status':'violated','error_type':'E1','correct_value':'999999',
                 'replacement_text':None,'evidence':['block'],'reason':'Controlled protocol fixture.'}]})}
        def close_session(self,sid): events.append(('close',sid))
        def close(self): events.append(('shutdown',None))
    import threading
    out=tmp_path/'baseline'; out.mkdir()
    dispatcher=SimpleNamespace(blocks={'block':{'id':'block','report_id':'report','page':1,'text':'营业收入100元'}},
        sessions={},register_session=lambda sid,role,mentions:dispatcher.sessions.update({sid:{'active':True}}))
    agent=SimpleNamespace(config=cfg,run_id='controlled-fixture',company='公司甲',text='100元',
        request={'report_date':'2025-03-31'},out=out,claims=[SimpleNamespace(to_dict=lambda:{'candidate_value':'100'})],
        dispatcher=dispatcher,resource_lock='controlled-fixture-lock',budget={'max_runtime_s':10},cancelled=threading.Event(),native_request_count=1,
        usage_known=False,total_tokens=0,audit=SimpleNamespace(emit=lambda *a,**k:None),
        redact=lambda value,**kwargs:value)
    agent.start_runtime=lambda:setattr(agent,'runtime',Runtime())
    if finish=='stop':
        result=runner.execute_model_baseline(agent,'direct-llm',12)
        assert result['trust']=='untrusted_baseline_only'
        assert result['results'][0]['correct_value']=='999999' and result['claims'][0]['candidate_value']=='100'
        assert result['model']['tokens']=='unknown'
    else:
        with pytest.raises(ValueError,match='complete'):
            runner.execute_model_baseline(agent,'direct-llm',12)
    assert [event[0] for event in events]==['open','prompt','close','shutdown']
    assert dispatcher.sessions['controlled-fixture-baseline']['active'] is False


def test_executable_signature_cannot_be_reused_for_another_draft_or_cutoff(material):
    _,row,_=material
    identity=runner.execution_task_identity(row)
    row['human_reviews']=[{'execution_task_sha256':runner.digest(identity)} for _ in range(3)]
    assert runner.require_executable_review_lock(row)==runner.digest(identity)
    for field,value in [('draft_text','Another draft'),('report_date','2025-04-01'),('scope_declaration','合并')]:
        changed=copy.deepcopy(row); changed[field]=value
        with pytest.raises(ValueError,match='identical executable'):
            runner.require_executable_review_lock(changed)


def test_oracle_preflight_rejects_multi_claim_before_any_execution(material):
    corpus,row,_=material
    row['gold_label']={'cells':[{'binding':{'company':row['company'],'account':'REVENUE','period':'2024Y',
        'scope':'合并','relation':'value','base_period':''}}]}
    runner.preflight_oracle_draft(row,corpus)
    row['draft_text'] += '公司甲2024年归母净利润为20000.00元。'
    with pytest.raises(ValueError,match='exactly one original draft claim'):
        runner.preflight_oracle_draft(row,corpus)


def test_scored_run_cannot_use_same_ids_with_a_different_signed_task(material,tmp_path,monkeypatch):
    corpus,row,cfg=material
    signed=copy.deepcopy(row)
    signed['human_reviews']=[{'execution_task_sha256':runner.digest(runner.execution_task_identity(signed))}
                              for _ in range(3)]
    monkeypatch.setattr(runner,'load_human_cases',lambda *args:[signed])
    changed=copy.deepcopy(row); changed['draft_text']='公司甲2024年营业收入为999999元。'
    manifest=tmp_path/'input.json'
    manifest.write_text(json.dumps({'schema_version':runner.SCHEMA,'cases':[changed],
        'experiments':[{'name':'kernel','baseline':'kernel'}]}),encoding='utf-8')
    called=[]
    monkeypatch.setattr(runner,'execute_case',lambda *args,**kwargs:called.append(True))
    with pytest.raises(ValueError,match='signed executable task'):
        runner.run_experiments(manifest,corpus,tmp_path/'out',cfg,lambda _:None,human_cases=tmp_path/'truth.json')
    assert not called and not (tmp_path/'out').exists()


@pytest.mark.parametrize('mismatch',['missing','seal','plan','gold','status','out'])
def test_sealed_executor_requires_matching_parent_exclusive_marker(tmp_path,monkeypatch,mismatch):
    seal={'seal_sha256':'sealed'}; plan={'plan_sha256':'planned'}
    expected={'human_gold_sha256':'gold'}
    monkeypatch.setattr(runner,'preflight_sealed_suite',lambda *args:expected)
    called=[]
    monkeypatch.setattr(runner,'run_experiments',lambda *args,**kwargs:called.append(True))
    out=tmp_path/'out'
    marker={'seal_sha256':'sealed','plan_sha256':'planned','gold_sha256':'gold','status':'started','out':str(out)}
    fields={'seal':'seal_sha256','plan':'plan_sha256','gold':'gold_sha256','status':'status','out':'out'}
    if mismatch!='missing': marker[fields[mismatch]]='wrong'
    marker_path=tmp_path/'attempt.json'; marker_path.write_text(json.dumps(marker),encoding='utf-8')
    with pytest.raises(ValueError,match='marker'):
        runner.execute_sealed_suite(seal,plan,tmp_path/'gold.json',tmp_path/'corpus',out,{},lambda _:None,
            attempt_marker=None if mismatch=='missing' else marker_path)
    assert not called and not out.exists()


def test_sealed_executor_projects_gold_outside_primary_execution(tmp_path,monkeypatch,material):
    _,row,_=material
    row['set']='D3'; row['gold_label']={'amount':'not-executable-secret'}
    seal={'seal_sha256':'sealed'}; plan={'plan_sha256':'planned','experiments':[{'name':'kernel','baseline':'kernel'}]}
    monkeypatch.setattr(runner,'preflight_sealed_suite',lambda *args:{'human_gold_sha256':'gold'})
    monkeypatch.setattr(runner,'load_human_cases',lambda *args:[row])
    out=tmp_path/'out'
    marker_path=tmp_path/'attempt.json'
    marker_path.write_text(json.dumps({'seal_sha256':'sealed','plan_sha256':'planned','gold_sha256':'gold',
                                     'status':'started','out':str(out)}),encoding='utf-8')
    def execute(manifest_path,*args,**kwargs):
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        assert manifest['cases']==[runner.execution_task_identity(row)]
        assert 'not-executable-secret' not in json.dumps(manifest)
        assert kwargs['_validated_holdout'] is seal
        return {'status_counts':{'executed':1}}
    monkeypatch.setattr(runner,'run_experiments',execute)
    result=runner.execute_sealed_suite(seal,plan,tmp_path/'gold.json',tmp_path/'corpus',out,{},lambda _:None,
                                      attempt_marker=marker_path)
    assert result['plan_sha256']=='planned' and (out/'sealed-suite-summary.json').is_file()


def test_metrics_count_failed_misses_and_unsafe_multi_output_and_preserve_unknown_usage():
    truths=[{'set':'B','gold_status':'violated','gold_error_type':'E1','gold_label':{'cells':[]}},
            {'set':'B','gold_status':'violated','gold_error_type':'E1','gold_label':{'cells':[]}},
            {'set':'C','gold_status':'verified','gold_label':{'cells':[]}}]
    records=[{'case_id':'a','status':'executed','elapsed_ms':10,'tokens':20,'model_requests':1,'results':[{'status':'violated','error_type':'E1'}]},
             {'case_id':'b','status':'failed','elapsed_ms':100,'tokens':'unknown','model_requests':'unknown','results':[]},
             {'case_id':'c','status':'executed','elapsed_ms':30,'tokens':10,'model_requests':1,'results':[{'status':'violated','error_type':'E1'}]}]
    scores=[runner.score_record(record,truth) for record,truth in zip(records,truths)]
    metrics=runner.summarize_experiment(records,scores)
    assert metrics['detection_precision']['rate']==metrics['detection_recall']['rate']==.5
    assert metrics['by_error_type']['E1']['f1']==.5
    assert metrics['strict_match']['rate']==1/3 and metrics['coverage']['rate']==2/3
    assert metrics['tokens']['total']=='unknown' and metrics['tokens']['known_total']==30
    assert metrics['elapsed_ms']['p90']==100
    multiple=runner.score_record({'case_id':'x','status':'partial','results':[{'status':'verified'},{'status':'verified'}]},truths[0])
    assert multiple['false_accept'] is True and multiple['usable_prediction'] is False
    assert runner.summarize_experiment([records[0]],[multiple])['false_accepts']==1
