"""Build controlled, official-source diagnostics without creating monetary truth."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from mingjing.evidence import sha256_file
from mingjing.parser import extract_text
from mingjing.source_gap_catalog import extract_source_gaps


def main():
    arguments=argparse.ArgumentParser(description=__doc__)
    arguments.add_argument('--corpus',type=Path,required=True)
    options=arguments.parse_args()
    extractor=Path(__file__).resolve().parents[1]/'mingjing/source_gap_catalog.py'
    extractor_hash=sha256_file(extractor)
    root=options.corpus.resolve(strict=True)
    manifest=json.loads((root/'material_manifest.json').read_text('utf-8'))
    path=root/'facts.json'
    payload=json.loads(path.read_text('utf-8'))
    if isinstance(payload,list):
        payload={'facts':payload}
    if not isinstance(payload,dict) or not isinstance(payload.get('facts'),list):
        raise ValueError('事实缓存不是可兼容的冻结结构')
    gaps=[]
    for document in manifest['documents']:
        if document.get('category') not in {'招股书','问询回复'}:
            continue
        source=(root/document['file']).resolve(strict=True)
        if not source.is_relative_to(root) or not source.is_file() or source.suffix.lower()!='.pdf':
            raise ValueError('官方来源路径越过已批准语料根或不是PDF')
        if sha256_file(source)!=document['sha256']:
            raise ValueError('官方披露PDF与冻结清单哈希不一致')
        if not document['date'] or document['date']>manifest['cutoff']:
            raise ValueError('来源缺少官方披露日期或超出回溯截止日')
        selected=extract_source_gaps(extract_text(source),path=source,company=document['name'],
            doc_sha=document['sha256'],disclosure_date=document['date'])
        gaps.extend(selected)
        print(document['announcement_id'],len(selected))
    payload['source_gaps']=list({row['id']:row for row in gaps}.values())
    if sha256_file(extractor)!=extractor_hash:
        raise ValueError('来源缺口提取器运行期间发生修改；冻结缓存不发布')
    payload['source_gap_catalog']={'schema_version':'mingjing-source-gaps.v1','cutoff':manifest['cutoff'],
        'material_manifest_sha256':sha256_file(root/'material_manifest.json'),
        'extractor_sha256':extractor_hash,
        'source_gap_count':len(payload['source_gaps']),
        'policy':'read_only_not_facts_no_candidate_value_matching'}
    temporary=root/'facts.source-gaps.pending.json'
    try:
        with temporary.open('w',encoding='utf-8') as stream:
            json.dump(payload,stream,ensure_ascii=False,indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if temporary.exists():
            temporary.unlink()
    print('facts',len(payload['facts']),'source_gaps',len(payload['source_gaps']))


if __name__=='__main__':
    main()
