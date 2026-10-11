from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
import re
import time
from dataclasses import replace
from pathlib import Path

from .audit import AuditLog, digest, finalize_manifest
from .facts import FactStore
from .judge import Judge
from .mentions import extract_mentions
from .models import Fact
from .parser import build_evidence, extract_text, load_or_extract_facts
from .report import write_reports


def _read_draft(path: Path, **control) -> str:
    if path.suffix.lower() == ".pdf":
        return "\n".join(page["text"] for page in extract_text(path, **control))
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _company_from_path(path: Path) -> str:
    for part in path.parts:
        if "_" in part and part.split("_", 1)[0].isdigit():
            return part.split("_", 1)[1]
    return ""


def _period_from_path(path: Path) -> str:
    name = path.name
    match = re.search(r"(20\d{2}).*?年度报告", name)
    if match:
        return f"{match.group(1)}Y"
    match = re.search(r"(20\d{2}).*?半年度报告", name)
    if match:
        return f"{match.group(1)}H1"
    match = re.search(r"(20\d{2}).*?三季度报告", name)
    if match:
        return f"{match.group(1)}Q3"
    match = re.search(r"(20\d{2}).*?一季度报告", name)
    if match:
        return f"{match.group(1)}Q1"
    return ""


def _semantic_filter_claims(text: str, claims: list, semantic: list[dict]) -> tuple[list, int]:
    """Map model source spans onto deterministic claims.

    The model is only a semantic selector.  Numeric values, units and verdicts
    continue to come from ``extract_mentions`` and the rule engine.
    """
    ranges: list[tuple[int, int]] = []
    # Keep a reversible whitespace-normalized view, so PDF line wrapping can
    # never turn a normalized index into an incorrect original-text offset.
    positions = [i for i, character in enumerate(text) if not character.isspace()]
    compact_text = "".join(text[i] for i in positions)
    for item in semantic:
        source = item.get("source") or item.get("text")
        if not isinstance(source, str) or not source.strip():
            continue
        source = source.strip()
        # Forecasts, valuation targets and market-price commentary are outside
        # the frozen financial fact set. Do not turn them into E1 violations
        # merely because they contain numbers.
        compact = "".join(source.split())
        if any(term in compact for term in ("预计", "预测", "目标价", "当前股价", "可比公司", "估值")):
            continue
        offset = 0
        while compact:
            match = compact_text.find(compact, offset)
            if match < 0:
                break
            ranges.append((positions[match], positions[match+len(compact)-1]+1))
            offset = match + len(compact)
    selected = []
    for claim in claims:
        if any(term in claim.text for term in ("预计", "预测", "目标价", "当前股价", "可比公司", "估值")):
            continue
        if any(claim.start < end and claim.end > start for start, end in ranges):
            selected.append(claim)
            continue
    return selected, len(selected)


def _deduplicate_claims(claims: list) -> list:
    seen = set()
    output = []
    for claim in claims:
        key = (claim.company, claim.account, claim.period, claim.caliber_hint,
               claim.relation, claim.base_period, claim.candidate_value, claim.unit,
               claim.display_decimals)
        if key not in seen:
            seen.add(key)
            output.append(claim)
    return output


def _checkpoint(args, phase: str, percent: int) -> None:
    event = getattr(args, "cancel_event", None)
    if event is not None and event.is_set():
        raise RuntimeError("run cancelled")
    deadline = getattr(args, "deadline_monotonic", None)
    if deadline is not None and time.monotonic() > deadline:
        raise TimeoutError("核查超过配置的总运行超时")
    callback = getattr(args, "progress_callback", None)
    if callback:
        callback(phase, percent=percent)


def command_check(args: argparse.Namespace) -> int:
    from .scope import validate_scope_declaration
    scope_declaration = validate_scope_declaration(getattr(args, 'scope_declaration', ''))
    from .clarifications import request_scope_clarifications
    grouped = getattr(args, 'scope_clarifications', None)
    if grouped is None:
        grouped = json.loads(getattr(args, 'scope_clarifications_json', '[]'))
    grouped = request_scope_clarifications({'scope_declaration': scope_declaration,
                                          'scope_clarifications': grouped})
    if getattr(args, 'progress_json', False) and not getattr(args, 'progress_callback', None):
        args.progress_callback = lambda name, **fields: print(json.dumps(
            {'schema_version': 'mingjing-progress.v1', 'name': name, **fields}, ensure_ascii=False), flush=True)
    if args.mode == 'harness':
        from .agent import FinancialAgent
        from .config import ConfigManager
        manager = ConfigManager()
        request = {key: getattr(args, key, None) for key in ('draft', 'corpus', 'report_date', 'company', 'period', 'facts', 'corpus_additions', 'knowledge_root')}
        request['task_type'] = getattr(args, 'task_type', 'check')
        request['scope_declaration'] = scope_declaration
        request['scope_clarifications'] = grouped
        request['reparse_packet_paths'] = getattr(args, 'reparse_packet_paths', None) or getattr(args, 'reparse_packet', [])
        request['reparse_parent_run'] = getattr(args, 'reparse_parent_run', None)
        request['reparse_inheritance'] = getattr(args, 'reparse_inheritance', None)
        if getattr(args, 'inherit_supplements_from', None):
            from .task_versions import plan_supplement_inheritance
            request['reparse_inheritance'] = plan_supplement_inheritance(args.inherit_supplements_from, request)
        result = FinancialAgent(request, manager.to_dict(), manager.get_secret, Path(args.out), run_id=args.run_id).run()
        print(json.dumps({'run_id': result['run_id'], 'summary': result['summary'], 'model': result['model']}, ensure_ascii=False))
        return 0
    if getattr(args, "run_timeout_s", 0) and not getattr(args, "deadline_monotonic", None):
        args.deadline_monotonic = time.monotonic() + args.run_timeout_s
    _checkpoint(args, "读取草稿", 5)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "evidence").mkdir(exist_ok=True)
    run_id = args.run_id or f"run-{uuid.uuid4().hex[:12]}"
    audit = AuditLog(out, run_id)
    draft_path = Path(args.draft)
    parsing = {key: value for key, value in dict(cancel_event=getattr(args, 'cancel_event', None),
                progress_callback=getattr(args, 'progress_callback', None),
                deadline_monotonic=getattr(args, 'deadline_monotonic', None)).items() if value is not None}
    text = _read_draft(draft_path, **parsing)
    audit.emit("file_access", path=str(draft_path), bytes=draft_path.stat().st_size, purpose="draft")
    corpus_path = Path(args.corpus)
    from .materials import normalize_corpus_additions
    additions = normalize_corpus_additions(getattr(args, 'corpus_additions', None), corpus_path)
    corpus_sources = [corpus_path, *(Path(path) for path in additions)]
    company = args.company or _company_from_path(draft_path) or _company_from_path(corpus_path)
    period = args.period or _period_from_path(corpus_path)
    _checkpoint(args, "建立事实库", 20)
    facts = load_or_extract_facts(args.corpus, company=company, period=period, publish_date='', **parsing)
    facts_from_fallback = not facts and bool(args.facts)
    if facts_from_fallback:
        facts = load_or_extract_facts(args.facts, company=company, period=period,
                                      publish_date='', **parsing)
    if company:
        facts = [fact for fact in facts if fact.company == company]
    # Structured fact caches use paths relative to their corpus directory so
    # the small offline demo remains portable after copying/unpacking.
    fact_base = Path(args.facts).parent if facts_from_fallback else corpus_path if corpus_path.is_dir() else corpus_path.parent
    facts = [replace(f, source_path=str((fact_base / f.source_path).resolve())) if f.source_path and not Path(f.source_path).is_absolute() else f for f in facts]
    for source in corpus_sources[1:]:
        _checkpoint(args, '读取补充财报材料', 20)
        supplemental = load_or_extract_facts(source, company=company, period=period, publish_date='', **parsing)
        base = source if source.is_dir() else source.parent
        facts.extend(replace(f, source_path=str((base / f.source_path).resolve()))
                     if f.source_path and not Path(f.source_path).is_absolute() else f for f in supplemental)
    if company:
        facts = [fact for fact in facts if fact.company == company]
    facts = FactStore(facts).facts
    # Demo/CLI convenience: a single-company facts file can supply the entity
    # when the draft path has no ticker directory.  This does not invent facts.
    if not company and facts:
        companies = {fact.company for fact in facts if fact.company}
        if len(companies) == 1:
            company = next(iter(companies))
        elif len(companies) > 1:
            raise ValueError('材料包含多家公司，请先明确选择一家核查或分析公司')
    from .reparse_inputs import apply_task_reparse_packets
    reparse_request = {key: getattr(args, key, None) for key in
                      ('draft', 'corpus', 'report_date', 'company', 'period', 'task_type', 'reparse_parent_run')}
    reparse_request.update(company=company, scope_declaration=scope_declaration,
        corpus_additions=additions,
        reparse_packet_paths=getattr(args, 'reparse_packet_paths', None) or getattr(args, 'reparse_packet', []),
        reparse_inheritance=getattr(args, 'reparse_inheritance', None))
    if getattr(args, 'inherit_supplements_from', None):
        from .task_versions import plan_supplement_inheritance
        reparse_request['reparse_inheritance'] = plan_supplement_inheritance(args.inherit_supplements_from, reparse_request)
    facts, reparse_supplements, supplement_locks = apply_task_reparse_packets(
        reparse_request, facts, cancel_event=getattr(args, 'cancel_event', None))
    if reparse_supplements:
        audit.emit('reparse_supplements_loaded', output=reparse_supplements,
                   source='locked_pdf_window_new_task_only')
    store = FactStore(facts)
    knowledge_registry = None
    knowledge_root = getattr(args, 'knowledge_root', '') or ''
    knowledge_inputs = []
    if knowledge_root:
        from .knowledge import KnowledgeRegistry, knowledge_resources, knowledge_summary
        _checkpoint(args, '建立外部会计知识库原文索引', 25)
        controls = {key: value for key, value in parsing.items() if key in {'cancel_event', 'deadline_monotonic'}}
        knowledge_registry = KnowledgeRegistry(knowledge_root, out/'knowledge-cache', **controls).scan()
        knowledge_root = str(knowledge_registry.root)
        from .agent import write_json
        knowledge_path = out/'knowledge_lock.json'
        write_json(knowledge_path, knowledge_registry.snapshot())
        knowledge_inputs = knowledge_resources(knowledge_registry, knowledge_path)
        for resource in knowledge_inputs:
            audit.emit('file_access', path=resource['path'], sha256=resource['sha256'], purpose='frozen_accounting_knowledge')
        audit.emit('knowledge_materials_locked', output=knowledge_summary(knowledge_registry, args.report_date), source='raw_references_only')
    from .source_registry import indexed_source_metadata
    indexed_metadata = {}
    for material in corpus_sources:
        indexed_metadata.update(indexed_source_metadata(material, company, **parsing))
    _checkpoint(args, "识别原子陈述", 40)
    from .coverage import CoverageLedger, partition_document
    source_text, pages, blocks = partition_document(draft_path, **parsing)
    ledger, claims = CoverageLedger.build(source_text, pages, blocks, company, period)
    from .evidence import sha256_file
    draft_sha256 = sha256_file(draft_path)
    from .source_table_reference import link_quoted_tables, validated_table_reference
    from .source_registry import canonical_report_resources
    cited_reports = [dict(report_id=row['id'], name=Path(row['path']).name,
                          companies=[row['company']], **row)
                     for row in canonical_report_resources(indexed_metadata.values())]
    gap_resources = canonical_report_resources(indexed_metadata.values())
    from .source_gaps import load_source_gaps, source_gap_summary
    source_gaps = load_source_gaps(corpus_sources, gap_resources, company, args.report_date, **parsing)
    claims, reference_diagnostics = link_quoted_tables(claims, source_text, facts, cited_reports, args.report_date, pages=pages)
    from .source_quarter_quotation import attach_threshold_references, validated_threshold_reference
    claims = attach_threshold_references(claims, source_text, pages, cited_reports, args.report_date, source_gaps)
    from .financial_derivations import attach_broker_compositions
    claims = attach_broker_compositions(claims)
    from .source_method import attach_source_methods
    claims = attach_source_methods(claims, source_text, pages, args.report_date)
    from .clarifications import prepare_scope_materials
    def archive_scope(path, value):
        from .agent import write_json
        write_json(path, value)
        return sha256_file(path)
    claims, clarification_resources = prepare_scope_materials(claims,
        {'scope_declaration': scope_declaration, 'scope_clarifications': grouped},
        draft_sha256=draft_sha256, output=out, archive=archive_scope)
    for clarification_resource in clarification_resources:
        audit.emit('clarification', provenance='user_supplied_scope', output=clarification_resource)
    # The offline kernel uses host-proven source dimensions, independently of
    # the optional dual-binder Agent. It must not retain an Agent-only pending
    # state after a deterministic reference has been admitted.
    claims = [replace(claim, binding_status='single') if (validated_table_reference(claim)
              or validated_threshold_reference(claim)) else claim for claim in claims]
    audit.emit('quoted_source_tables', linked_claim_ids=[claim.claim_id for claim in claims
        if validated_table_reference(claim)], diagnostics=reference_diagnostics)
    from .source_semantics import SCHEMA, freeze_source_dimensions
    claims = [freeze_source_dimensions(replace(claim, table_context={**claim.table_context, 'source_scope_required': True}))
              for claim in claims]
    source_dimensions_path = out / 'source_dimensions.json'
    source_dimensions_path.write_text(json.dumps({'schema_version': SCHEMA, 'draft_sha256': draft_sha256,
        'dimensions': [{'claim_id': claim.claim_id, **claim.table_context['source_dimensions']} for claim in claims]},
        ensure_ascii=False, indent=2), encoding='utf-8')
    source_dimensions_resource = {'resource_id': 'source-dimensions', 'schema_version': SCHEMA,
                                 'sha256': sha256_file(source_dimensions_path), 'claim_count': len(claims)}
    audit.emit('source_dimensions_locked', output=source_dimensions_resource,
               source='deterministic_original_text_before_model_binding')
    model_result = {"status": "not_requested", "mode": "kernel"}
    if args.mode == "agent":
        try:
            from .llm import decompose_with_metadata
            semantic, metadata = decompose_with_metadata(text)
            filtered, mapped = _semantic_filter_claims(text, claims, semantic)
            # A valid semantic response that cannot be mapped is not allowed
            # to produce an empty report; retain deterministic claims and make
            # the reason explicit in the audit trail.
            if not filtered:
                raise ValueError("模型返回的来源片段无法定位到有效金融陈述")
            # Keep all occurrences in the coverage ledger. A model omission
            # cannot erase a financial claim from the task.
            # ``decompose_with_metadata`` already carries the resolved model;
            # avoid passing duplicate keyword fields into AuditLog.emit.
            metadata = dict(metadata)
            metadata.setdefault("model", os.getenv("MINGJING_LLM_MODEL"))
            model_result = {"status": "success", "mode": "agent", "semantic_claims": len(semantic), "mapped_claims": mapped, **metadata}
            audit.emit("model_call", status="success", input_hash=digest(text), output_hash=digest(semantic), semantic_claims=len(semantic), mapped_claims=mapped, **metadata)
        except Exception as exc:
            model_result = {"status": "fallback", "mode": "kernel", "model": os.getenv("MINGJING_LLM_MODEL"), "error": str(exc), "fallback": "deterministic_extractor"}
            audit.emit("model_call", model=os.getenv("MINGJING_LLM_MODEL"), status="fallback", error=str(exc), input_hash=digest(text), fallback="deterministic_extractor")
    _checkpoint(args, "确定性核查", 65)
    audit.emit("tool_call", tool="claim_decompose", input={"draft_sha256": digest(text)}, output={"claims": len(claims)})
    results = []
    judge = Judge(store, args.report_date)
    for claim in claims:
        _checkpoint(args, "确定性核查", 65)
        result = judge.verify(claim).to_dict()
        result["claim_text"] = claim.text
        result["source_span"] = {"start": claim.start, "end": claim.end}
        results.append(result)
        ledger.complete(claim.claim_id, result)
        audit.emit("judgement", claim_id=claim.claim_id, input=claim.to_dict(), output=result, evidence=result.get("evidence", []), status=result.get("status"))
    summary = {key: sum(1 for item in results if item["status"] == key) for key in ("verified", "violated", "abstained")}
    _checkpoint(args, "生成报告与审计", 90)
    payload = {"run_id": run_id, "draft": str(draft_path), "draft_text": text, "company": company, "period": args.period or '', "corpus": str(corpus_path), "corpus_additions": additions, "report_date": args.report_date, "mode": args.mode, "task_type": getattr(args, 'task_type', 'check'), "model": model_result, "summary": summary, "claims": [claim.to_dict() for claim in claims], "facts": store.to_list(), "results": results}
    payload['scope_declaration'] = scope_declaration
    from .knowledge import knowledge_summary
    payload['knowledge_root'] = knowledge_root
    payload['knowledge'] = knowledge_summary(knowledge_registry, args.report_date)
    payload['reparse_supplements'] = reparse_supplements
    from .reparse_inputs import project_reparse_facts
    payload['reparse_fact_views'] = project_reparse_facts(facts, reparse_supplements)
    payload['clarifications'] = clarification_resources
    payload['scope_clarifications'] = grouped
    from .clarifications import scope_clarification_groups
    payload['scope_clarification_groups'] = scope_clarification_groups(claims, results)
    payload['source_dimensions_resource'] = source_dimensions_resource
    from .source_registry import canonical_report_resources
    gap_resources = canonical_report_resources(indexed_metadata.values())
    payload['source_registry'] = {row['id']: row for row in gap_resources}
    payload['source_gaps'] = source_gaps
    audit.emit('source_gap_catalogue', output=source_gap_summary(payload['source_gaps']))
    payload['coverage'] = ledger.to_dict()
    payload['partial'] = payload['coverage']['partial']
    (out / 'coverage.json').write_text(json.dumps(payload['coverage'], ensure_ascii=False, indent=2), encoding='utf-8')
    if getattr(args, 'task_type', 'check') == 'analysis':
        from .analysis import analyze_financials
        from .disclosures import disclosures_from_facts
        payload['accounting_disclosures'] = disclosures_from_facts(facts, control=parsing)
        payload['analysis'] = analyze_financials(store, company, sorted({f.period for f in facts if f.company == company}), args.report_date,
                                               scope_declaration or '合并', disclosures=payload['accounting_disclosures'])
    from .artifacts import export_evidence
    analysis = payload.get('analysis') or {}
    evidence_items = results + analysis.get('metrics', []) + analysis.get('signals', []) + analysis.get('events', [])
    supplement_ids = {fact_id for row in reparse_supplements for fact_id in row['added_fact_ids']}
    evidence_items.extend({'evidence': list(fact.evidence)} for fact in facts if fact.fact_id in supplement_ids)
    payload['evidence_resources'] = export_evidence(facts, evidence_items, out)
    from .source_gap_evidence import export_source_gap_evidence, merge_source_gap_evidence
    gap_evidence = export_source_gap_evidence(payload['source_gaps'], gap_resources, out)
    merge_source_gap_evidence(payload['evidence_resources'], gap_evidence)
    audit.emit('source_gap_page_evidence', output={'rendered': len(gap_evidence), 'authorizes_judgement': False})
    if payload.get('accounting_disclosures'):
        from .disclosures import export_disclosure_evidence
        payload['evidence_resources'].update(export_disclosure_evidence(payload['accounting_disclosures'], out))
    audit.emit('tool_call', tool='export_evidence', output={'rendered':len(payload['evidence_resources'])})
    from .coverage_stats import attach_coverage_statistics
    attach_coverage_statistics(payload)
    audit.emit('financial_coverage_statistics', output=payload['coverage_statistics'])
    (out / "claims.json").write_text(json.dumps(payload["claims"], ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "facts.json").write_text(json.dumps(payload["facts"], ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    inputs = [str(draft_path)] + [fact.source_path for fact in facts if fact.source_path]
    inputs.extend(path for row in indexed_metadata.values() for path in (row['path'], row['index_path']))
    inputs.extend(row['source_manifest_path'] for row in indexed_metadata.values() if row.get('source_manifest_path'))
    inputs.extend(row['path'] for row in supplement_locks)
    inputs.extend(row['path'] for row in knowledge_inputs)
    inputs.append(str(source_dimensions_path.resolve()))
    inputs.extend(resource['path'] for resource in clarification_resources)
    corpus_resources = [resource for source in corpus_sources for resource in
                        ([source] if source.is_file() else [source / 'facts.json', source / 'index.csv'])]
    for resource in [*corpus_resources, Path(args.facts) if args.facts else None]:
        if resource and resource.is_file():
            inputs.append(str(resource))
    write_reports(payload, out)
    audit.session("run_complete", summary=summary)
    finalize_manifest(out, run_id, inputs, args.mode, {"facts_count": len(facts), "claims_count": len(claims), "summary": summary})
    _checkpoint(args, "完成", 100)
    print(json.dumps({"run_id": run_id, "out": str(out), "summary": summary}, ensure_ascii=False))
    return 0


def command_replay(args: argparse.Namespace) -> int:
    if getattr(args, 'relocation_map', None):
        if not args.out:
            raise ValueError('--relocation-map requires a separate --out below evidence/relocation')
        from .relocation_replay import replay_relocated
        output = replay_relocated(mode=args.replay_mode, run=args.run, relocation_map=args.relocation_map,
                                  out=args.out, session_file=args.session_file, message_id=args.message_id)
    else:
        if getattr(args, 'replay_mode', 'kernel') != 'kernel':
            raise ValueError('Explicit agent/message relocation replay requires --relocation-map')
        from .replay import replay_run
        output = replay_run(args.run)
    print(json.dumps(output, ensure_ascii=False))
    return 0 if output["status"] == "passed" else 1


def command_evidence(args: argparse.Namespace) -> int:
    root = Path(args.run)
    data = json.loads((root / "results.json").read_text(encoding="utf-8"))
    wanted = args.evidence_id
    for fact in data.get("facts", []):
        if wanted in fact.get("evidence", []):
            print(json.dumps({"evidence_id": wanted, "fact": fact}, ensure_ascii=False, indent=2))
            return 0
    print(json.dumps({"evidence_id": wanted, "found": False}, ensure_ascii=False))
    return 1


def command_rules(args: argparse.Namespace) -> int:
    from .rules import validate_rulepack
    result = validate_rulepack()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'ok' else 1


def command_serve(args: argparse.Namespace) -> int:
    path = Path(args.results).resolve()
    try:
        import streamlit.web.cli as stcli  # type: ignore
        os.environ["MINGJING_RESULTS"] = str(path)
        sys.argv = ["streamlit", "run", str(Path(__file__).with_name("streamlit_app.py")), "--server.headless", "true"]
        stcli.main()
    except ImportError:
        import webbrowser
        webbrowser.open(path.with_name("report.html").as_uri())
        print(f"streamlit 未安装，已尝试打开 {path.with_name('report.html')}")
    return 0


def command_package(args: argparse.Namespace) -> int:
    # Publishing requires matching source/build/acceptance receipts. Never
    # sweep a working repository (including personal files) into an archive.
    import subprocess
    root = Path(__file__).resolve().parents[1]
    from .import_integrity import assert_application_origin
    assert_application_origin(root)
    options = list(args.package_args)
    if options[:1] == ['--']:
        options = options[1:]
    return subprocess.call([sys.executable, '-m', 'scripts.package_delivery', *options], cwd=root)


def command_smart(args: argparse.Namespace) -> int:
    """直传模式：给文件即可核查/分析，公司、期间等自动识别；材料自动进入知识库并去重。"""
    from .smart.kb import KnowledgeBase
    from .smart.pipeline import ingest, run_task
    kb = KnowledgeBase(args.kb or None)
    if args.smart_command == 'replay':
        from .smart.pipeline import replay
        receipt = replay(args.files[0], kb)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0 if receipt['status'] == 'identical' else 1
    if args.smart_command == 'kb':
        if getattr(args, 'remove', ''):
            matches = [r for r in kb.list() if r['doc_id'].startswith(args.remove.strip().lower())]
            if len(matches) != 1:
                print('未找到该文档，或编号前缀对应多份文档；请用 --list 查看完整编号', file=sys.stderr)
                return 2
            kb.remove(matches[0]['doc_id'])
            print(json.dumps({'removed': matches[0]['name'], 'doc_id': matches[0]['doc_id']}, ensure_ascii=False))
            return 0
        if getattr(args, 'verify', False):
            receipt = kb.verify_journal()
            print(json.dumps(receipt, ensure_ascii=False, indent=2))
            return 0 if receipt['ok'] else 1
        if args.files:
            for record in ingest(args.files, kb, origin='cli'):
                print(json.dumps({k: record.get(k) for k in ('name', 'type_label', 'company', 'period', 'report_date', 'duplicate', 'status')}, ensure_ascii=False))
        if getattr(args, 'list', False):
            for record in kb.list():
                print('\t'.join([record['doc_id'][:12], record.get('type_label', ''), record.get('company', ''), record.get('stock_code', ''),
                                 record.get('period', ''), record.get('report_date', ''), str(record.get('facts', '')), record['name']]))
            return 0
        print(json.dumps(kb.stats(), ensure_ascii=False, indent=2))
        return 0
    hints = {key: getattr(args, key) for key in ('company', 'period', 'report_date') if getattr(args, key, '')}
    records = ingest(args.files, kb, hints=hints, origin='cli')
    options = {'strict': args.strict, 'kb_lookup': not args.no_kb, **{k: v for k, v in hints.items() if k != 'period'}}
    if getattr(args, 'llm', False):
        from .config import LocalConfig
        from .smart.llm_bind import config_from_app
        local = LocalConfig()
        llm = config_from_app(local.get(), local.secret)
        if llm.get('enabled'):
            options['llm'] = llm
        else:
            print('未在明镜配置中找到可用的模型，已按纯内核运行（可在高级界面配置供应商与模型）', file=sys.stderr)
    progress = (lambda name, pct: print(f'[{pct:3d}%] {name}', flush=True)) if not args.quiet else None
    batch = run_task([r['doc_id'] for r in records], kb, args.out, mode=args.smart_command if args.smart_command in ('check', 'analysis') else 'auto',
                     options=options, progress=progress)
    print(json.dumps({'run_id': batch['run_id'], 'out': batch['out'], 'task': batch['task'], 'items': batch['items'], 'failures': batch['failures']}, ensure_ascii=False, indent=2))
    return 1 if batch['failures'] and not batch['items'] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mingjing", description="明镜金融研究报告事实核查")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--draft", required=True)
    check.add_argument("--corpus", required=True)
    check.add_argument('--corpus-addition', dest='corpus_additions', action='append', default=[], help='追加财报文件或目录；保留主语料')
    check.add_argument('--reparse-packet', dest='reparse_packet', action='append', default=[],
                       help='应用冻结父任务生成的局部补证包；只用于新任务版本')
    check.add_argument('--reparse-parent-run', help='补证包所属的冻结父任务目录')
    check.add_argument('--inherit-supplements-from', help='从冻结版本继承补证；逐包重新核验原PDF页和材料/规则锁')
    check.add_argument("--report-date", default="")
    check.add_argument("--period", default="")
    check.add_argument("--company", default="")
    check.add_argument("--facts")
    check.add_argument('--knowledge-root', default='', help='唯一外部会计知识库目录；只加载原文引用，不替换签发规则')
    check.add_argument("--out", required=True)
    check.add_argument("--mode", choices=("kernel", "agent", "harness", "replay"), default="kernel")
    check.add_argument("--task-type", choices=('check', 'analysis'), default='check')
    check.add_argument("--scope-declaration", choices=('', '合并', '母公司'), default='', help='用户口径澄清；默认仅按原文')
    check.add_argument('--scope-clarifications-json', default='[]', help='仅对选定原文提及、指标或表补充口径的 JSON 数组；创建条件核查')
    check.add_argument("--run-id")
    check.add_argument('--progress-json', action='store_true', help=argparse.SUPPRESS)
    check.add_argument('--run-timeout-s', type=float, default=0, help=argparse.SUPPRESS)
    check.set_defaults(func=command_check)
    replay = sub.add_parser("replay")
    replay.add_argument("--run", required=True)
    replay.add_argument('--relocation-map', help='Explicit read-only workspace location map; never rewrites saved locks')
    replay.add_argument('--out', help='New relocation replay output below evidence/relocation')
    replay.add_argument('--replay-mode', choices=('kernel', 'agent', 'message'), default='kernel')
    replay.add_argument('--session-file', help='Frozen session JSON for message relocation replay')
    replay.add_argument('--message-id', help='Frozen message identifier for message relocation replay')
    replay.set_defaults(func=command_replay)
    evidence = sub.add_parser("evidence"); evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True); show = evidence_sub.add_parser("show"); show.add_argument("evidence_id"); show.add_argument("--run", required=True); show.set_defaults(func=command_evidence)
    rules = sub.add_parser("rules"); rules_sub = rules.add_subparsers(dest="rules_command", required=True); check_rules = rules_sub.add_parser("check"); check_rules.set_defaults(func=command_rules)
    serve = sub.add_parser("serve"); serve.add_argument("--results", required=True); serve.set_defaults(func=command_serve)
    package = sub.add_parser("package"); package.add_argument('package_args', nargs=argparse.REMAINDER); package.set_defaults(func=command_package)
    smart = sub.add_parser("smart", help="直传模式：上传文件即可核查/分析，自动识别公司与期间，材料进入知识库")
    smart_sub = smart.add_subparsers(dest="smart_command", required=True)
    for name, text in (("run", "按识别结果自动选择核查或分析"), ("check", "研报核查"), ("analysis", "财报分析"), ("kb", "知识库入库与统计"),
                       ("replay", "离线重放：用同一批输入重算并逐条比较")):
        item = smart_sub.add_parser(name, help=text)
        item.add_argument("files", nargs="*" if name == "kb" else "+", help="研报/财报文件或目录，可多个" if name != "replay" else "运行结果目录（含 results.json）")
        item.add_argument("--kb", default="", help="知识库目录，默认 MINGJING_HOME/kb")
        if name == "kb":
            item.add_argument("--list", action="store_true", help="列出知识库中的文档（编号前12位、类型、公司、期间、日期、事实数、文件名）")
            item.add_argument("--remove", default="", help="按文档编号（或唯一前缀）从知识库删除原件与解析缓存")
            item.add_argument("--verify", action="store_true", help="校验留痕日志哈希链是否完整")
        if name not in ("kb", "replay"):
            item.add_argument("--llm", action="store_true", help="使用明镜配置中的模型做语义绑定与摘要")
            item.add_argument("--out", default="runs", help="输出根目录")
            item.add_argument("--company", default="", help="可选：覆盖自动识别的公司")
            item.add_argument("--period", default="", help="可选：覆盖自动识别的报告期")
            item.add_argument("--report-date", dest="report_date", default="", help="可选：覆盖自动识别的研报日期")
            item.add_argument("--strict", action="store_true", help="审慎模式：不做口径推定，备选定义命中不算核实")
            item.add_argument("--no-kb", action="store_true", help="不从知识库补充同公司材料")
            item.add_argument("--quiet", action="store_true")
        item.set_defaults(func=command_smart)
    return parser


def main(argv: list[str] | None = None) -> int:
    # The same UTF-8 contract applies to a Windows console and redirected
    # subprocess output; platform locale must not corrupt Chinese JSON.
    import sys
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
