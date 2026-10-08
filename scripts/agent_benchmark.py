"""Run real deterministic pipelines against explicitly synthetic test fixtures.

Independent human truth, model binding quality and D3 are separate gates; no
generated numeric test is labeled original-PDF truth or an unseen holdout.
"""
from __future__ import annotations
import argparse
from dataclasses import replace
from decimal import Decimal
from datetime import datetime, timezone
import hashlib
import itertools
import json
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from bench.v1.generated import generate_suite, declared_source
from bench.stats import wilson_interval
from mingjing.facts import FactStore, fact_from_dict
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.models import AtomicClaim
from mingjing.audit import digest


def ratio(k,n):
    return {'numerator':k,'denominator':n,'rate':k/n if n else None,
            'wilson95':list(wilson_interval(k,n)) if n else None}


def _percentile(values,p=0.9):
    ordered=sorted(values)
    return ordered[min(len(ordered)-1,max(0,int(len(ordered)*p+0.999999)-1))] if ordered else None


def _claims(text,company,period):
    claims=extract_mentions(text,company=company,fallback_period=period)
    if not claims and '营业收入' in text:
        # Explicitly unsupported financial expressions remain visible, rather
        # than disappearing from the inventory. No value is guessed.
        return [AtomicClaim('unparsed-mention',text,0,len(text),company=company,account='REVENUE',
                            period=period,parse_status='unparsed',candidate_value=None)]
    citation=declared_source(text)
    return [replace(c,declared_source=citation) for c in claims]


def run_case(case,*,strict=False,confidence='0.95',dual=False,approximate=None,hedge_relative='0.02',cutoff=True):
    started=time.perf_counter()
    claims=_claims(case['draft'],case['company'],case.get('period',''))
    facts=FactStore([fact_from_dict(row) for row in case.get('facts',[])])
    claims=[replace(c,binding_status=case.get('binding_fixture_status','agreed')) for c in claims]
    judge=Judge(facts,case.get('report_date') if cutoff else None,strict_caliber=strict,
                confidence_threshold=confidence,require_dual_binding=dual,approximate_multiplier=approximate,
                hedge_relative=hedge_relative)
    outputs=[judge.verify(c).to_dict() for c in claims]
    result=outputs[0] if len(outputs)==1 else {'status':'abstained','error_type':'E8','abstention_code':None,
                                             'reason':'没有抽取唯一陈述','evidence':[],'correction':None}
    repair={'attempted':False,'qualified':False,'rechecked':False}
    if result.get('status')=='violated' and result.get('correction'):
        correction=result['correction']
        repair['attempted']=True
        repair['qualified']=all(correction.get(key) for key in ('correct_value','source','calculation','basis','replacement_text'))
        repaired=_claims(correction.get('replacement_text',''),case['company'],case.get('period',''))
        repaired=[replace(c,binding_status='agreed') for c in repaired]
        repair['rechecked']=bool(repaired) and all(judge.verify(c).status=='verified' for c in repaired)
        repair['text']=correction.get('replacement_text')
    expected=case['gold_status']
    strict_match=result.get('status')==expected and result.get('error_type')==case.get('gold_error_type')
    if expected=='verified': strict_match=result.get('status')=='verified'
    if case.get('gold_abstention_code'): strict_match=strict_match and result.get('abstention_code')==case['gold_abstention_code']
    allowed={e for fact in facts.facts for e in fact.evidence}
    evidence_ok=bool(result.get('evidence')) and set(result['evidence'])<=allowed
    return {'id':case['id'],'set':case['set'],'variant':case.get('variant'),'gold_origin':case['gold_origin'],
            'gold_status':expected,'gold_error_type':case.get('gold_error_type'),'gold_abstention_code':case.get('gold_abstention_code'),
            'prediction':result.get('status'),'predicted_error_type':result.get('error_type'),
            'abstention_code':result.get('abstention_code'),'advisories':result.get('advisories',[]),
            'strict_match':strict_match,'claims_extracted':len(claims),'logical_evidence_ok':evidence_ok,
            'evidence_kind':case.get('evidence_kind'),'repair':repair,'result':result,
            'source_spans':[{'start':c.start,'end':c.end,'numeric_text':c.numeric_text} for c in claims],
            'elapsed_ms':(time.perf_counter()-started)*1000,'tokens':0,'model_requests':0,'model_cost':None}


def evaluate(records):
    erroneous=[r for r in records if r['gold_status']=='violated']
    predicted=[r for r in records if r['prediction']=='violated']
    true=[r for r in erroneous if r['prediction']=='violated']
    traps=[r for r in records if r['set']=='C']
    abstention=[r for r in records if r['gold_status']=='abstained']
    decidable=[r for r in records if r['gold_status'] in {'verified','violated'}]
    corrections=[r for r in records if r['repair']['attempted']]
    by_type={}
    for type_ in sorted({r['gold_error_type'] for r in records if r['gold_error_type']}):
        positives=[r for r in records if r['gold_error_type']==type_ and r['gold_status']=='violated']
        output=[r for r in records if r['predicted_error_type']==type_ and r['prediction']=='violated']
        tp=sum(r['gold_error_type']==type_ and r['gold_status']=='violated' for r in output)
        p=tp/len(output) if output else 0; rec=tp/len(positives) if positives else 0
        by_type[type_]={'precision':ratio(tp,len(output)),'recall':ratio(tp,len(positives)),
                       'f1':2*p*rec/(p+rec) if p+rec else 0,
                       'classification':ratio(sum(r['strict_match'] for r in records if r['gold_error_type']==type_),sum(r['gold_error_type']==type_ for r in records)),
                       'note':'E8属于材料不可验证，按正确弃权单列' if type_=='E8' else None}
    return {'total':len(records),'strict_match':ratio(sum(r['strict_match'] for r in records),len(records)),
            'false_accepts':sum(r['prediction']=='verified' for r in erroneous),
            'false_accept_interval':ratio(sum(r['prediction']=='verified' for r in erroneous),len(erroneous)),
            'detection_precision':ratio(len(true),len(predicted)),'detection_recall':ratio(len(true),len(erroneous)),
            'trap_false_positive':ratio(sum(r['prediction']=='violated' for r in traps),len(traps)),
            'correct_abstention_code':ratio(sum(r['strict_match'] for r in abstention),len(abstention)),
            'unnecessary_abstention':ratio(sum(r['prediction']=='abstained' for r in decidable),len(decidable)),
            'coverage':ratio(sum(r['prediction']!='abstained' for r in records),len(records)),
            'error_coverage':ratio(len(true),len(erroneous)), 'by_error_type':by_type,
            'one_statement_extraction':ratio(sum(r['claims_extracted']==1 for r in records),len(records)),
            'logical_evidence_chain':ratio(sum(r['logical_evidence_ok'] for r in records if r['prediction']!='abstained'),sum(r['prediction']!='abstained' for r in records)),
            'evidence_page':{'status':'not_measured_synthetic_fixtures'},'evidence_table':{'status':'not_measured_synthetic_fixtures'},'evidence_cell':{'status':'not_measured_synthetic_fixtures'},
            'correction_qualified':ratio(sum(r['repair']['qualified'] for r in corrections),len(corrections)),
            'repair_rechecked':ratio(sum(r['repair']['rechecked'] for r in corrections),len(corrections)),
            'binding_accuracy':{'status':'not_measured_model_binding_not_run'},
            'binding_stability':{'status':'not_measured_real_models_not_run'},
            'double_blind_agreement':{'status':'not_measured_independent_sessions_not_run'},
            'latency_ms_mean':statistics.mean(r['elapsed_ms'] for r in records) if records else None,
            'latency_ms_p90':_percentile([r['elapsed_ms'] for r in records]),'tokens':0,'model_requests':0,
            'model_cost':{'status':'unknown','reason':'No real provider usage or registered price table'},
            'gold_origin_counts':{origin:sum(r['gold_origin']==origin for r in records) for origin in sorted({r['gold_origin'] for r in records})}}


def _write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def load_human_cases(path,corpus_root):
    """Validate attested human data; absence is a blocked gate, not fake gold."""
    root=Path(corpus_root).resolve()
    raw=Path(path).read_text(encoding='utf-8')
    rows=[json.loads(line) for line in raw.splitlines() if line.strip()] if Path(path).suffix=='.jsonl' else json.loads(raw)
    ids=set()
    for row in rows:
        if row.get('id') in ids: raise ValueError('人工真值编号重复')
        ids.add(row.get('id'))
        if row.get('gold_origin')!='independent_human_original_pdf' or any(key in row for key in ('prediction','predictions','result')):
            raise ValueError('不得以生成或预测记录作为人工原PDF真值')
        from bench.v1.human_review import label_key, validate_review
        reviews=row.get('human_reviews',[])
        if (not isinstance(reviews, list) or len(reviews) != 3 or
                len({r.get('reviewer', {}).get('id') for r in reviews if isinstance(r,dict)}) != 3 or
                any(not isinstance(r,dict) or r.get('reviewer',{}).get('reviewer_kind') != 'human' for r in reviews)):
            raise ValueError('须三名独立人类完整审核签认；模型或空label不能替代')
        task = {key: row[key] for key in ('id','set','company','source_documents') if key in row}
        for review in reviews:
            validate_review(review, task, row.get('task_sha256'), root)
        labels = {digest(label_key(review['label'])) for review in reviews}
        if len(labels)!=1: raise ValueError('人工标注存在分歧，须由真人记录删除或重标')
        if not isinstance(row.get('gold_label'),dict) or digest(label_key(row['gold_label'])) not in labels:
            raise ValueError('人工金标金额、绑定、版本与定位必须匹配完整签认')
        for field,key in (('status','gold_status'),('error_type','gold_error_type'),('abstention_code','gold_abstention_code')):
            if row['gold_label'].get(field) != row.get(key):
                raise ValueError('人工金标标签与完整签认不一致')
        if row.get('evidence_kind')!='original_pdf': raise ValueError('人工真值须保留原PDF证据')
        sources=row.get('source_documents',[])
        if not sources: raise ValueError('人工真值缺少原文档哈希')
        for source in sources:
            target=(root/source['path']).resolve()
            if not target.is_relative_to(root) or not target.is_file() or target.suffix.lower()!='.pdf':
                raise ValueError('人工真值原PDF路径非法')
            if hashlib.sha256(target.read_bytes()).hexdigest()!=source['sha256']:
                raise ValueError('人工真值原PDF哈希不符')
    return rows


def run_benchmark(out,*,scan=False):
    out=Path(out)
    if (out/'metrics.json').exists():
        raise ValueError('禁止覆盖既有评测；选择新的输出目录')
    out.mkdir(parents=True,exist_ok=True)
    cases=generate_suite()
    with (out/'generated_cases.jsonl').open('w',encoding='utf-8') as stream:
        for row in cases: stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    records=[run_case(case) for case in cases]
    with (out/'predictions.jsonl').open('w',encoding='utf-8') as stream:
        for row in records: stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    metrics=evaluate(records)
    metrics.update({'schema_version':'crrv-v2-generated-validation','status':'generated_pipeline_measured_not_professional_acceptance',
                    'suite_sha256':digest(cases),'records_sha256':digest(records),'code_fingerprint':source_fingerprint(),
                    'subset_counts':{set_:sum(c['set']==set_ for c in cases) for set_ in ('P','A','B','C','X','D3')},
                    'unmet_requirements':[{'set':'P','status':'not_executed','reason':'三家公司含重述的独立人工真值未登记'},
                                          {'set':'A','status':'not_executed','reason':'20公司两年15指标未完成独立人工原PDF审核'},
                                          {'set':'B/C/X','status':'human_signoff_pending','reason':'本轮为程序化已知fixture，不是三名人类盲标真值'},
                                          {'set':'D3','status':'not_executed','reason':'新系统冻结后独立封存/人工真值/一次性正式评测尚未完成'}]})
    # These ablations rerun the actual parser and Judge. Provider baselines
    # remain explicitly unexecuted; no cached labels pretend to be an API.
    metrics['ablations']={}
    for name,options in [('single_binding',{'dual':False}),('dual_binding_fixture',{'dual':True}),('cutoff_disabled',{'cutoff':False}),('strict_caliber',{'strict':True})]:
        subset=[run_case(case,**options) for case in cases]
        metrics['ablations'][name]={'metrics':evaluate(subset),'prediction_hash':digest(subset),
                                     'binding_source':'fixture_metadata_not_independent_models'}
    metrics['baselines']={'rule_pipeline':{'status':'executed','records':len(records)},
                          'pure_model':{'status':'not_executed','reason':'需真实API及同一材料的独立实验'},
                          'retrieval_model':{'status':'not_executed','reason':'需真实API及证据检索实验'},
                          'harness_agent':{'status':'not_executed','reason':'需原生工具循环与独立session集成后实验'}}
    if scan:
        points=[]
        for fuzzy,confidence,strict,dual in itertools.product(('0.005','0.01','0.02','0.05'),('0.8','0.95'),(False,True),(False,True)):
            scored=[run_case(case,hedge_relative=fuzzy,confidence=confidence,strict=strict,dual=dual) for case in cases]
            points.append({'parameters':{'hedge_relative':fuzzy,'confidence_threshold':confidence,'strict_caliber':strict,'dual':dual},
                           'metrics':evaluate(scored),'prediction_hash':digest(scored),'execution':'real_pipeline_rerun'})
        metrics['working_points']=points
        valid=[p for p in points if p['metrics']['false_accepts']==0]
        metrics['zero_false_accept_max_coverage']=max((p['metrics']['coverage']['rate'] for p in valid),default=None)
    _write_json(out/'metrics.json',metrics)
    _write_json(out/'measurement_audit.json',{'kind':'generated_pipeline_measurement','metrics_sha256':hashlib.sha256((out/'metrics.json').read_bytes()).hexdigest(),
                                             'input_sha256':hashlib.sha256((out/'generated_cases.jsonl').read_bytes()).hexdigest(),
                                             'predictions_sha256':hashlib.sha256((out/'predictions.jsonl').read_bytes()).hexdigest(),
                                             'created_at':datetime.now(timezone.utc).isoformat(),'professional_acceptance':False})
    return metrics


def source_fingerprint():
    files=[]
    for directory in ('mingjing','rulepack','harness','scripts','bench/v1'):
        for path in sorted((ROOT/directory).rglob('*')):
            if path.is_file() and path.suffix in {'.py','.json','.md','.yaml','.yml','.ts','.js','.html','.css','.ps1','.spec'} and not any(part in {'vendor','node_modules','__pycache__'} for part in path.relative_to(ROOT).parts):
                files.append((path.relative_to(ROOT).as_posix(),hashlib.sha256(path.read_bytes()).hexdigest()))
    return digest(files)


def used_tickers():
    used={'920493'}  # Known development/real-API acceptance issuer.
    for name in ('holdout_seal_manifest.json','holdout2_seal_manifest.json'):
        path=ROOT/'bench'/name
        if path.exists():
            used.update(str(row['ticker']) for row in json.loads(path.read_text(encoding='utf-8'))['reports'])
    # A/P preparation reads original documents and therefore consumes those
    # issuers for development, even though no human gold or evaluation exists.
    exposures=sorted((ROOT/'bench/v1').glob('annotations*/development_exposure_registry.json'))
    for exposure in exposures:
        registry=json.loads(exposure.read_text(encoding='utf-8'))
        recorded=set(registry.get('exposed_tickers',[]))
        recorded.update(row.get('ticker') for row in registry.get('sources',[]))
        if any(not isinstance(ticker,str) or len(ticker)!=6 or not ticker.isdigit() for ticker in recorded):
            raise ValueError('A/P开发暴露登记包含非法公司代码；不得静默丢弃污染保护')
        used.update(recorded)
    return used


def seal_d3(corpus,out,*,reports=None,model_training_cutoff=None,experiment_plan=None):
    """Hash reports without reading their truth; reject already viewed issuers."""
    root=Path(corpus).resolve(); target=Path(out)
    if target.exists(): raise ValueError('禁止覆盖D3封存')
    excluded=used_tickers()
    import re
    annual_caption=re.compile(r'^20\d{2}-\d{2}-\d{2}_20\d{2}年(?:年度|年年度)报告(?:[（(](?:更正(?:后|版)?|修订(?:后|版)?|更新(?:后|版)?|修订稿)[）)])?\.pdf$')
    if experiment_plan is not None:
        if (not isinstance(experiment_plan,dict)
                or experiment_plan.get('schema_version')!='mingjing-experiment-preregister.v1'
                or experiment_plan.get('plan_sha256')!=digest({key:value for key,value in experiment_plan.items() if key!='plan_sha256'})):
            raise ValueError('实验预注册计划损坏，不封存D3')
    if reports is None:
        candidates={}
        for path in sorted(root.rglob('*.pdf')):
            issuer=next((part for part in path.parts if '_' in part and part.split('_',1)[0].isdigit()),None)
            if not issuer or not annual_caption.fullmatch(path.name): continue
            ticker=issuer.split('_',1)[0]
            if ticker in excluded: continue
            if ticker not in candidates or path.name > candidates[ticker].name:
                candidates[ticker]=path
        reports=list(candidates.values())[:5]
    rows=[]
    for raw in reports:
        path=Path(raw).resolve()
        if not path.is_relative_to(root) or not path.is_file(): raise ValueError('D3报告不在已登记语料根中')
        if not annual_caption.fullmatch(path.name): raise ValueError('D3只能封存完整财政年度年报，不能使用制度、摘要或公告填数')
        issuer=next((part for part in path.parts if '_' in part and part.split('_',1)[0].isdigit()),None)
        if not issuer: raise ValueError('D3公司元数据缺失')
        ticker,company=issuer.split('_',1)
        if ticker in excluded: raise ValueError('已使用D/D2、A/P或开发公司不可作为D3')
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append({'ticker':ticker,'company':company,'path':str(path),'sha256':sha,'bytes':path.stat().st_size})
    if len({r['ticker'] for r in rows})!=5: raise ValueError('D3须五家独立未参与开发公司')
    seal={'schema_version':'crrv-d3-preregister.v1','sealed_at':datetime.now(timezone.utc).isoformat(),
          'reports':rows,'excluded_tickers':sorted(excluded),'code_fingerprint':source_fingerprint(),
          'gold_status':'not_created','human_review_status':'pending','one_shot_evaluation_status':'not_executed',
          'experiment_plan':experiment_plan,
          'experiment_plan_status':'registered' if experiment_plan else 'missing_not_executable',
          'model_training_cutoff':model_training_cutoff or 'unknown',
          'temporal_claim':'无法证明晚于未知模型训练截止日' if not model_training_cutoff else 'requires_report_period_review'}
    seal['seal_sha256']=digest(seal)
    target.parent.mkdir(parents=True,exist_ok=True); _write_json(target,seal)
    return seal


def validate_d3_seal(path, corpus):
    """Check preregistration, exact source bytes and frozen code before exposure."""
    raw=json.loads(Path(path).read_text(encoding='utf-8'))
    if raw.get('schema_version')!='crrv-d3-preregister.v1':
        raise ValueError('不是D3预注册封存')
    body={key:value for key,value in raw.items() if key!='seal_sha256'}
    if digest(body)!=raw.get('seal_sha256'):
        raise ValueError('D3封存哈希不符')
    if raw.get('code_fingerprint')!=source_fingerprint():
        raise ValueError('代码或规则在D3封存后改变；不得使用此封存作最终冻结验收')
    root=Path(corpus).resolve()
    seen=set()
    for report in raw.get('reports',[]):
        source=Path(report['path']).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            raise ValueError('D3原文档不在已登记语料根')
        if hashlib.sha256(source.read_bytes()).hexdigest()!=report['sha256']:
            raise ValueError('D3原文档哈希改变')
        if report['ticker'] in used_tickers() or report['ticker'] in seen:
            raise ValueError('D3公司被重复使用或不是新独立公司')
        seen.add(report['ticker'])
    if len(seen)!=5:
        raise ValueError('D3须五家独立公司')
    return raw


def evaluate_d3(seal_path, human_cases, corpus, out, *, config=None, secret_resolver=None):
    """Run the preregistered complete matrix once, after non-consuming preflight.

    The former rule-only workflow is deliberately unavailable here. Missing
    plan, credentials, signed truth or executable inputs cannot consume D3.
    """
    seal_path=Path(seal_path)
    seal=validate_d3_seal(seal_path,corpus)
    attempt=seal_path.with_name(seal_path.stem+'.attempt.json')
    if attempt.exists():
        raise ValueError('D3已有正式尝试；失败也不得重复调参重跑同一保留集')
    target=Path(out)
    if target.exists():
        raise ValueError('禁止覆盖D3正式评测目录')
    plan=seal.get('experiment_plan')
    if not plan:
        raise ValueError('D3缺完整baseline/ablation预注册；不得以仅规则流水线消耗正式机会')
    if config is None:
        from mingjing.config import ConfigManager
        manager=ConfigManager()
        config=manager.to_dict()
        secret_resolver=secret_resolver or manager.get_secret
    if secret_resolver is None:
        raise ValueError('D3缺受控密钥解析器，正式尝试未创建')
    from scripts.experiment_runner import preflight_sealed_suite, execute_sealed_suite
    preflight=preflight_sealed_suite(seal,plan,human_cases,corpus,config,secret_resolver)
    marker={'seal_sha256':seal['seal_sha256'],'started_at':datetime.now(timezone.utc).isoformat(),
            'gold_sha256':hashlib.sha256(Path(human_cases).read_bytes()).hexdigest(),
            'plan_sha256':plan['plan_sha256'],
            'code_fingerprint':source_fingerprint(),'status':'started','out':str(target.resolve())}
    try:
        with attempt.open('x',encoding='utf-8') as stream:
            stream.write(json.dumps(marker,ensure_ascii=False,indent=2)+'\n')
    except FileExistsError as exc:
        raise ValueError('D3已有正式尝试；失败也不得重复调参重跑同一保留集') from exc
    target.mkdir(parents=True)
    try:
        metrics=execute_sealed_suite(seal,plan,human_cases,corpus,target,config,secret_resolver,attempt_marker=attempt)
        metrics.update({'schema_version':'crrv-d3-once.v2','status':'one_shot_registered_matrix_measured',
                        'seal_sha256':seal['seal_sha256'],'gold_sha256':marker['gold_sha256'],
                        'code_fingerprint':marker['code_fingerprint'],'human_gold_count':preflight['case_count'],
                        'companies':preflight['participating_tickers'],'model_training_cutoff':seal['model_training_cutoff'],
                        'temporal_claim':seal['temporal_claim'],
                        'agent_acceptance':False,'reason':'Complete release gates and independent full-report occurrence scoring remain separate'})
        _write_json(target/'metrics.json',metrics)
        marker['status']='completed_with_failures' if any(metrics.get('status_counts',{}).get(key,0) for key in ('failed','partial','fallback')) else 'completed'
        marker['metrics_sha256']=hashlib.sha256((target/'metrics.json').read_bytes()).hexdigest()
        _write_json(target/'execution_audit.json',marker)
        _write_json(attempt,marker)
        return metrics
    except BaseException as exc:
        marker['status']='failed_consumed'
        marker['exception_type']=type(exc).__name__
        _write_json(target/'execution_audit.json',marker)
        _write_json(attempt,marker)
        raise


def main(argv=None):
    parser=argparse.ArgumentParser(description='明镜可审计生成测试与D3预注册')
    parser.add_argument('--out',required=True); parser.add_argument('--scan',action='store_true')
    parser.add_argument('--seal-d3',action='store_true'); parser.add_argument('--corpus')
    parser.add_argument('--evaluate-d3'); parser.add_argument('--human-cases')
    parser.add_argument('--experiment-plan'); parser.add_argument('--config')
    args=parser.parse_args(argv)
    if args.evaluate_d3:
        if not args.corpus or not args.human_cases: parser.error('--evaluate-d3需要--corpus和--human-cases')
        options={}
        if args.config:
            from mingjing.config import ConfigManager
            manager=ConfigManager(args.config)
            options={'config':manager.to_dict(),'secret_resolver':manager.get_secret}
        result=evaluate_d3(args.evaluate_d3,args.human_cases,args.corpus,args.out,**options)
        print(json.dumps({'status':result['status'],'experiments':result.get('experiments'),
                          'execution_counts':result.get('status_counts'),'agent_acceptance':False},ensure_ascii=False))
    elif args.seal_d3:
        if not args.corpus: parser.error('--seal-d3需要--corpus')
        result=seal_d3(args.corpus,args.out,experiment_plan=json.loads(Path(args.experiment_plan).read_text(encoding='utf-8')) if args.experiment_plan else None)
        print(json.dumps({'status':'sealed_not_evaluated','seal_sha256':result['seal_sha256']},ensure_ascii=False))
    else:
        result=run_benchmark(args.out,scan=args.scan)
        print(json.dumps({'status':result['status'],'counts':result['subset_counts'],'strict':result['strict_match'],'false_accepts':result['false_accepts']},ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
