from collections import Counter
from pathlib import Path
import json

import pytest

from bench.v1.generated import generate_suite
from scripts import agent_benchmark as benchmark
from mingjing.judge import Judge


def test_generated_suite_has_all_error_pairs_and_distinct_human_status():
    cases=generate_suite()
    assert Counter(c['set'] for c in cases)=={'B':270,'C':60,'X':24}
    for error in (f'E{i}' for i in range(1,10)):
        matched=[c for c in cases if c['set']=='B' and c['gold_error_type']==error]
        assert Counter(c['variant'] for c in matched)=={'raw':15,'rounded':15}
    assert all(c['gold_origin']=='deterministic_generated_fixture' and not c['human_reviews'] for c in cases)
    assert {c['gold_abstention_code'] for c in cases if c['set']=='X'}=={f'X{i}' for i in range(1,7)}


def test_predictions_are_produced_by_judge_not_copied_from_gold(tmp_path,monkeypatch):
    actual=Judge.verify
    calls=[]
    def change(self,claim):
        calls.append(claim.claim_id)
        result=actual(self,claim)
        if result.status=='violated':
            result.error_type='E1'
        return result
    monkeypatch.setattr(Judge,'verify',change)
    report=benchmark.run_benchmark(tmp_path/'run')
    assert calls
    assert report['strict_match']['rate']<1
    assert report['model_requests']==0
    assert report['binding_accuracy']['status'].startswith('not_measured')
    assert report['evidence_cell']['status']=='not_measured_synthetic_fixtures'
    assert report['baselines']['pure_model']['status']=='not_executed'
    assert {item['set'] for item in report['unmet_requirements']}=={'P','A','B/C/X','D3'}
    with pytest.raises(ValueError,match='禁止覆盖'):
        benchmark.run_benchmark(tmp_path/'run')


def test_working_points_rerun_real_pipeline(tmp_path,monkeypatch):
    original=benchmark.run_case
    calls=[]
    def wrapper(case,**kwargs):
        calls.append(kwargs)
        return original(case,**kwargs)
    monkeypatch.setattr(benchmark,'run_case',wrapper)
    report=benchmark.run_benchmark(tmp_path,scan=True)
    assert len(report['working_points'])==32
    assert len(calls)>32*354
    assert {p['parameters']['hedge_relative'] for p in report['working_points']}=={'0.005','0.01','0.02','0.05'}
    assert all(p['execution']=='real_pipeline_rerun' for p in report['working_points'])
    assert any(call.get('cutoff') is False for call in calls)


def fake_reports(root,tickers):
    result=[]
    for ticker in tickers:
        path=root/f'{ticker}_测试公司'/ '年报'/'2024-04-01_2023年年度报告.pdf'
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture-pdf-not-real-truth')
        result.append(path)
    return result


def test_d3_rejects_used_cohort_and_requires_five_issuers(tmp_path):
    corpus=tmp_path/'corpus'
    old=next(iter(benchmark.used_tickers()))
    reports=fake_reports(corpus,[old,'999001','999002','999003','999004'])
    with pytest.raises(ValueError,match='不可作为D3'):
        benchmark.seal_d3(corpus,tmp_path/'invalid.json',reports=reports)
    fresh=fake_reports(corpus,['999011','999012','999013','999014','999015'])
    seal=benchmark.seal_d3(corpus,tmp_path/'seal.json',reports=fresh)
    assert seal['gold_status']=='not_created' and seal['one_shot_evaluation_status']=='not_executed'
    assert seal['model_training_cutoff']=='unknown'
    assert len(seal['reports'])==5
    with pytest.raises(ValueError,match='禁止覆盖'):
        benchmark.seal_d3(corpus,tmp_path/'seal.json',reports=fresh)


def test_d3_rejects_actual_ap_development_exposure_without_opening_its_sources(tmp_path):
    registry_path=benchmark.ROOT/'bench/v1/annotations_v2/development_exposure_registry.json'
    registry=json.loads(registry_path.read_text(encoding='utf-8'))
    exposed=set(registry['exposed_tickers'])
    assert len(exposed)==21 and exposed<=benchmark.used_tickers()
    # Use isolated fake files for rejection; never access original A/P or D3 PDFs.
    ticker=next(t for t in sorted(exposed) if t!='920493')
    corpus=tmp_path/'fixture-corpus'
    reports=fake_reports(corpus,[ticker,'999031','999032','999033','999034'])
    with pytest.raises(ValueError,match='A/P'):
        benchmark.seal_d3(corpus,tmp_path/'must-not-seal.json',reports=reports)
    assert not (tmp_path/'must-not-seal.json').exists()


def test_agents_or_generated_fixtures_cannot_be_claimed_as_human_truth(tmp_path):
    path=tmp_path/'human.json'
    path.write_text(json.dumps([generate_suite()[0]]),encoding='utf-8')
    with pytest.raises(ValueError,match='不得以生成'):
        benchmark.load_human_cases(path,tmp_path)
    row=generate_suite()[0]
    row['gold_origin']='independent_human_original_pdf'
    row['human_reviews']=[{'reviewer_id':str(i),'reviewer_kind':'model','attested':True,'label':'verified'} for i in range(3)]
    path.write_text(json.dumps([row]),encoding='utf-8')
    with pytest.raises(ValueError,match='人类'):
        benchmark.load_human_cases(path,tmp_path)


def _matrix_fixture():
    config={'revision':1,'agent':{'routing':{'default':{'provider':'fixture','model':'a'}}},
            'providers':{'fixture':{'enabled':True,'material_access':True,'base_url':'http://127.0.0.1',
                'credential_ref':'fixture_ref','models':[{'id':'a'},{'id':'b'}]}}}
    experiments=[{'name':name,'baseline':baseline} for name,baseline in
                 [('kernel','kernel'),('exact','rule-exact-match'),('direct','direct-llm'),('rag','rag')]]
    experiments += [{'name':'native-'+model,'baseline':'harness','route':{'provider':'fixture','model':model},'repetitions':3}
                    for model in ('a','b')]
    experiments += [{'name':'oracle','baseline':'kernel','ablation':'oracle-binding'},
                    {'name':'single','baseline':'harness','ablation':'single-binding'},
                    {'name':'date','baseline':'kernel','ablation':'cutoff-disabled'},
                    {'name':'point','baseline':'kernel','ablation':'point-tolerance'}]
    return config,experiments


def _signed_validation_fixture(seal,corpus):
    # Bookkeeping-only fixture, not a real attestation or professional gold.
    rows=[]
    for index,source in enumerate(seal['reports']):
        rows.append({'id':str(index),'set':'D3','draft':'2023年营业收入为100元。',
            'company':source['company'],'period':'2023Y','report_date':'2024-04-15',
            'gold_status':'abstained','gold_error_type':'E8','gold_abstention_code':'X1',
            'gold_origin':'independent_human_original_pdf','evidence_kind':'original_pdf',
            'gold_label':{'cells':[{'binding':{'company':source['company'],'account':'REVENUE','period':'2023Y',
                                              'scope':'合并','relation':'value','base_period':''}}]},'human_reviews':[],
            'source_documents':[{'path':str(Path(source['path']).relative_to(corpus)),'sha256':source['sha256']}]})
        from scripts.experiment_runner import execution_task_identity
        rows[-1]['human_reviews'] = [{'execution_task_sha256': benchmark.digest(execution_task_identity(rows[-1]))}
                                     for _ in range(3)]
    return rows


def test_d3_checks_code_and_documents_and_delegates_full_matrix_once(tmp_path,monkeypatch):
    from scripts import experiment_runner as runner
    corpus=tmp_path/'corpus'
    reports=fake_reports(corpus,['999021','999022','999023','999024','999025'])
    config,experiments=_matrix_fixture()
    plan=runner.preregister_experiments(experiments,config)
    seal_path=tmp_path/'seal.json'
    seal=benchmark.seal_d3(corpus,seal_path,reports=reports,experiment_plan=plan)
    assert benchmark.validate_d3_seal(seal_path,corpus)['seal_sha256']==seal['seal_sha256']
    original=benchmark.source_fingerprint
    monkeypatch.setattr(benchmark,'source_fingerprint',lambda:'changed')
    with pytest.raises(ValueError,match='改变'):
        benchmark.validate_d3_seal(seal_path,corpus)
    monkeypatch.setattr(benchmark,'source_fingerprint',original)
    rows=_signed_validation_fixture(seal,corpus)
    gold=tmp_path/'fixture-human-validation.json'
    gold.write_text(json.dumps(rows),encoding='utf-8')
    # This test covers seal/one-attempt bookkeeping, not human attestation.
    # Full real-PDF review validation has its own malformed/complete tests.
    monkeypatch.setattr(runner,'load_human_cases',lambda *_:rows)
    executions=[]
    def execute(sealed,registered,truth,material,out,cfg,secrets,*,attempt_marker):
        marker=json.loads(Path(attempt_marker).read_text(encoding='utf-8'))
        assert marker['status']=='started' and marker['plan_sha256']==registered['plan_sha256']
        assert marker['seal_sha256']==sealed['seal_sha256']
        executions.append(registered['experiments'])
        return {'status_counts':{'executed':70},'experiments':{},'full_report_mention_recall':{'status':'unknown'}}
    monkeypatch.setattr(runner,'execute_sealed_suite',execute)
    result=benchmark.evaluate_d3(seal_path,gold,corpus,tmp_path/'first',config=config,secret_resolver=lambda _: 'controlled-fixture')
    assert len(executions)==1 and {e['baseline'] for e in executions[0]}==runner.BASELINES
    assert result['status']=='one_shot_registered_matrix_measured'
    assert result['agent_acceptance'] is False
    assert json.loads(seal_path.with_name('seal.attempt.json').read_text(encoding='utf-8'))['status']=='completed'
    with pytest.raises(ValueError,match='已有正式尝试'):
        benchmark.evaluate_d3(seal_path,gold,corpus,tmp_path/'second',config=config,secret_resolver=lambda _: 'controlled-fixture')


@pytest.mark.parametrize('failure',['no-plan','incomplete-matrix','no-secret','no-signing','facts','wrong-draft',
                                    'other-company','unsigned-draft','multi-claim'])
def test_d3_preflight_failures_never_create_attempt_or_run_models(tmp_path,monkeypatch,failure):
    from scripts import experiment_runner as runner
    corpus=tmp_path/'corpus'
    reports=fake_reports(corpus,['999041','999042','999043','999044','999045'])
    config,experiments=_matrix_fixture()
    if failure=='incomplete-matrix': experiments=experiments[:1]
    plan=None if failure=='no-plan' else runner.preregister_experiments(experiments,config)
    seal_path=tmp_path/'seal.json'
    seal=benchmark.seal_d3(corpus,seal_path,reports=reports,experiment_plan=plan)
    rows=_signed_validation_fixture(seal,corpus)
    if failure=='facts': rows[0]['facts']=[]
    elif failure=='wrong-draft': rows[0].pop('draft')
    elif failure=='other-company': rows[0]['company']='Other company'
    elif failure=='unsigned-draft': rows[0]['human_reviews'][0]['execution_task_sha256']='0'*64
    elif failure=='multi-claim':
        rows[0]['draft'] += '2023年归母净利润为20元。'
        for signed in rows[0]['human_reviews']:
            signed['execution_task_sha256']=benchmark.digest(runner.execution_task_identity(rows[0]))
    gold=tmp_path/'controlled-validation-fixture.json'
    gold.write_text(json.dumps(rows),encoding='utf-8')
    if failure!='no-signing': monkeypatch.setattr(runner,'load_human_cases',lambda *_:rows)
    called=[]
    monkeypatch.setattr(runner,'execute_sealed_suite',lambda *a,**k: called.append(True))
    with pytest.raises(ValueError):
        benchmark.evaluate_d3(seal_path,gold,corpus,tmp_path/'out',config=config,
                              secret_resolver=lambda _: None if failure=='no-secret' else 'controlled-fixture')
    assert not called and not seal_path.with_name('seal.attempt.json').exists() and not (tmp_path/'out').exists()


def test_d3_execution_failure_consumes_formal_attempt(tmp_path,monkeypatch):
    from scripts import experiment_runner as runner
    corpus=tmp_path/'corpus'
    reports=fake_reports(corpus,['999051','999052','999053','999054','999055'])
    config,experiments=_matrix_fixture()
    plan=runner.preregister_experiments(experiments,config)
    seal_path=tmp_path/'seal.json'
    seal=benchmark.seal_d3(corpus,seal_path,reports=reports,experiment_plan=plan)
    rows=_signed_validation_fixture(seal,corpus)
    gold=tmp_path/'controlled-validation-fixture.json'
    gold.write_text(json.dumps(rows),encoding='utf-8')
    monkeypatch.setattr(runner,'load_human_cases',lambda *_:rows)
    def failed(*a,**k): raise RuntimeError('controlled-fixture')
    monkeypatch.setattr(runner,'execute_sealed_suite',failed)
    with pytest.raises(RuntimeError):
        benchmark.evaluate_d3(seal_path,gold,corpus,tmp_path/'out',config=config,secret_resolver=lambda _: 'controlled-fixture')
    marker=json.loads(seal_path.with_name('seal.attempt.json').read_text(encoding='utf-8'))
    assert marker['status']=='failed_consumed' and marker['exception_type']=='RuntimeError'
    with pytest.raises(ValueError,match='已有正式尝试'):
        benchmark.evaluate_d3(seal_path,gold,corpus,tmp_path/'next',config=config,secret_resolver=lambda _: 'controlled-fixture')


def test_d3_does_not_seal_a_policy_document_as_an_annual_report(tmp_path):
    corpus=tmp_path/'corpus'
    reports=fake_reports(corpus,['999061','999062','999063','999064','999065'])
    policy=reports[0].with_name('2024-06-01_年度报告重大差错责任追究制度.pdf')
    policy.write_bytes(b'controlled-policy-fixture')
    with pytest.raises(ValueError,match='完整财政年度年报'):
        benchmark.seal_d3(corpus,tmp_path/'seal.json',reports=[policy,*reports[1:]])
    assert not (tmp_path/'seal.json').exists()
