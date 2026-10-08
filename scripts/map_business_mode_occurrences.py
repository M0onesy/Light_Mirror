"""Read-only exact-character mapping of a local live receipt to the full draft."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from mingjing.agent import write_json
from mingjing.evidence import sha256_file
from scripts.source_web_acceptance import new_output


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def execute(local, historical, manifest, out):
    files=[local/'results.json',local/'source_acceptance.json',historical/'results.json',
           historical/'agent_lock.json',manifest]
    before={str(p.resolve()):sha256_file(p) for p in files}
    small,live,full,lock,excerpt=[read(p) for p in files]
    draft=next(r for r in lock['resources'] if r['id']=='draft')
    if draft['sha256']!=excerpt['original_sha256'] or sha256_file(draft['path'])!=draft['sha256']:
        raise ValueError('Historical draft is not the locked original of this excerpt')
    before[str(Path(draft['path']).resolve())]=draft['sha256']
    if sha256_file(small['draft'])!=excerpt['excerpt_sha256']:
        raise ValueError('Executed local draft differs from the frozen excerpt')
    results={r['claim_id']:r for r in small['results']}
    historical_results={r['claim_id']:r for r in full['results']}
    mappings=[]
    for claim in small['claims']:
        reference=claim.get('table_context',{}).get('source_table_reference',{})
        if reference.get('quotation_kind')!='business_mode_narrative':
            continue
        text=claim['text']
        positions=[];start=0
        while True:
            position=full['draft_text'].find(text,start)
            if position<0:break
            positions.append(position);start=position+len(text)
        if len(positions)!=1 or type(claim.get('source_numeric_start')) is not int:
            raise ValueError('Complete unmodified narrative cannot be uniquely located in the historical draft')
        paragraph_start=positions[0]
        span=(paragraph_start+claim['source_numeric_start'],paragraph_start+claim['source_numeric_start']+len(claim['numeric_text']))
        originals=[c for c in full['claims'] if (c['start'],c['end'])==span]
        if (len(originals)!=1 or full['draft_text'][span[0]:span[1]]!=claim['numeric_text']
                or originals[0]['numeric_text']!=claim['numeric_text']):
            raise ValueError('Original financial occurrence is missing or not byte-for-character identical')
        original=originals[0]
        pages=[p for p in full['coverage']['pages'] if p['start']<=span[0]<span[1]<=p['end']]
        if len(pages)!=1:
            raise ValueError('Original occurrence lacks a unique page')
        mapped={'local_claim_id':claim['claim_id'],'original_claim_id':original['claim_id'],
            'original_page':pages[0]['page'],'original_span':list(span),'local_span':[claim['start'],claim['end']],
            'numeric_text':claim['numeric_text'],'complete_narrative_sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'complete_narrative_original_span':[paragraph_start,paragraph_start+len(text)],
            'mapping_basis':'unique_full_original_paragraph_exact_characters_then_relative_numeric_span',
            'original_recorded_status':historical_results[original['claim_id']]['status'],
            'local_live_status':results[claim['claim_id']]['status'],
            'source_table_reference':reference,'evidence':results[claim['claim_id']].get('evidence',[]),
            'advisories':results[claim['claim_id']].get('advisories',[])}
        mappings.append(mapped)
    if len(mappings)!=6 or len({r['original_claim_id'] for r in mappings})!=6:
        raise ValueError('The six financial source occurrences are not mapped one-to-one')
    if any(sha256_file(p)!=h for p,h in before.items()):
        raise ValueError('Read-only source material changed during mapping')
    out=new_output(out)
    receipt={'status':'mapped_read_only','historical_run_unchanged':True,'source_hashes':before,
        'original_draft_sha256':draft['sha256'],'mappings':mappings,
        'mapped_original_occurrences':len(mappings),
        'original_recorded_counts':dict(Counter(r['original_recorded_status'] for r in mappings)),
        'local_live_counts':dict(Counter(r['local_live_status'] for r in mappings)),
        'local_model_run':live.get('model_run'),'local_kernel_replay':live.get('kernel_replay'),
        'local_agent_replay':live.get('agent_replay'),
        'full_report_reexecution':False,'full_report_acceptance':False,
        'scope':'original_occurrence_receipt_link_only'}
    write_json(out/'mapping.json',receipt)
    lines=['# 第31页真实小样本与原研报位置映射','',
        '映射按完整原段落唯一定位和原始数字相对字符区间完成；未按金额或判定择源，未修改历史结果。','',
        '| 本次条目 | 原任务条目 | 原页 | 原字符区间 | 原记录 | 本次真实运行 |',
        '|---|---|---:|---|---|---|']
    for row in mappings:
        lines.append(f"| {row['local_claim_id']} | {row['original_claim_id']} | {row['original_page']} | {row['original_span']} | {row['original_recorded_status']} | {row['local_live_status']} |")
    lines+=['','三个占比只核实明确引用的来源表转述；原文未独立明示分母，A4及限定说明保留。',
        '这份收据不代表全文已重新执行或最终金融验收通过。','']
    (out/'mapping.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({k:receipt[k] for k in ('status','mapped_original_occurrences','original_recorded_counts','local_live_counts')},ensure_ascii=False))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--local',type=Path,default=ROOT/'runs/source-business-mode-37-local')
    p.add_argument('--historical',type=Path,default=ROOT/'runs/source-real-stable-16-full')
    p.add_argument('--manifest',type=Path,default=ROOT/'runs/diagnostics/original-p31-business-mode-20261008/excerpt_manifest.json')
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();execute(a.local,a.historical,a.manifest,a.out)


if __name__=='__main__':main()
