"""Frozen source material with unresolved financial definitions is not a Fact.

The host may expose its provenance for supplemental-material work. Nothing in
this catalogue authorises a value, scope, formula or verdict.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import re

from .audit import digest


def load_source_gaps(materials, resources, company, cutoff, **control):
    from .parser import _read_text, _parse_checkpoint, _file_hash, extract_text
    from .source_gap_catalog import extract_source_gaps
    reports = {row['sha256']: row for row in resources
               if row.get('id', '').startswith('report-') and row.get('sha256')}
    catalog = {}
    for material in materials:
        material = Path(material)
        path = material if material.suffix.lower() in {'.json', '.jsonl'} else material / 'facts.json'
        if path.is_file() and path.suffix.lower() == '.json':
            _parse_checkpoint(**control)
            payload = json.loads(_read_text(path, **control))
            rows = payload.get('source_gaps', []) if isinstance(payload, dict) else []
            if not isinstance(rows,list):
                raise ValueError('Source gap catalogue must be a list of source records')
            # Diagnostic cache text has always been inert. It cannot be
            # upgraded to authoritative predicates by a new rule release,
            # even if its record borrows a real document hash/evidence ID.
            rows = [row for row in rows if not (isinstance(row,dict) and
                    str(row.get('gap_kind','')).startswith('source-threshold'))]
            selected = [r for r in reports.values() if r.get('path') and
                        Path(r['path']).resolve().is_relative_to((material.parent if material.is_file() else material).resolve())]
            from .source_quarter_quotation import extract_source_gaps as threshold_gaps
            for report in selected:
                source=Path(report['path'])
                if (not report.get('publish_date') or source.suffix.lower()!='.pdf'
                        or not re.search(r'第(?:二|2)轮.*问询.*回复',source.name)):
                    continue
                if _file_hash(source,**control)!=report['sha256']:
                    raise ValueError('Source assertion report changed from its frozen hash')
                rows.extend(threshold_gaps(extract_text(source,**control),path=source,company=company,
                    doc_sha=report['sha256'],disclosure_date=report['publish_date']))
                if _file_hash(source,**control)!=report['sha256']:
                    raise ValueError('Source assertion report changed during parsing')
        else:
            # Selected PDFs must expose the same source diagnostics as a
            # frozen fact-cache generation. Only already registered reports
            # are read; neither file names nor report prose supply dates.
            selected = [r for r in reports.values() if r.get('path') and
                        (Path(r['path']).resolve() == material.resolve() if material.is_file()
                         else Path(r['path']).resolve().is_relative_to(material.resolve()))]
            rows = []
            for report in selected:
                _parse_checkpoint(**control)
                published = report.get('publish_date') or ''
                if not published:
                    continue
                source = Path(report['path'])
                if source.suffix.lower() != '.pdf':
                    continue
                if _file_hash(source, **control) != report['sha256']:
                    raise ValueError('Source diagnostic report changed from its frozen hash')
                rows.extend(extract_source_gaps(extract_text(source, **control), path=source,
                    company=company, doc_sha=report['sha256'], disclosure_date=published))
                if _file_hash(source, **control) != report['sha256']:
                    raise ValueError('Source diagnostic report changed during parsing')
        if not isinstance(rows, list):
            raise ValueError('Source gap catalogue must be a list of source records')
        for raw in rows:
            _parse_checkpoint(**control)
            if not isinstance(raw, dict):
                raise ValueError('Source gap record must be an object')
            if raw.get('company') != company:
                continue
            identifier, doc_sha = raw.get('id'), raw.get('doc_sha256')
            if (not isinstance(identifier, str) or not re.fullmatch(r'sg-[a-f0-9]{24}', identifier)
                    or not isinstance(doc_sha, str) or not re.fullmatch(r'[a-f0-9]{64}', doc_sha)
                    or doc_sha not in reports):
                raise ValueError('Source gap must refer to a registered, hash-locked report')
            report = reports[doc_sha]
            evidence = raw.get('evidence', [])
            if not isinstance(evidence, list) or not all(isinstance(e, str) and
                    re.fullmatch(re.escape(doc_sha[:12])+r':p[1-9]\d*:[^:]+:[a-f0-9]+', e) for e in evidence):
                raise ValueError('Source gap evidence must belong to its original report')
            # Free-form fields are archived as inert source text. They never
            # become execution arguments or financial values.
            row = {key: raw.get(key) for key in ('id', 'doc_sha256', 'company', 'account', 'period',
                'dimension_kind', 'dimension_id', 'currency', 'gap_kind', 'reason', 'table_label', 'source_context')}
            row.update(scope='', report_id=report['id'], evidence=evidence,
                       publish_date=report.get('publish_date') or '',
                       status='source_definition_unresolved', eligible_for_verification=False,
                       needed_method=raw.get('needed_method') or raw.get('reason') or '明确来源口径与指标定义')
            if str(raw.get('gap_kind','')).startswith('source-threshold'):
                row['assertion_origin']='host_pdf_extract'
            if not isinstance(row['needed_method'], str):
                raise ValueError('Source gap method must be an inert text description')
            if row['publish_date']:
                try:
                    published = date.fromisoformat(row['publish_date'])
                except (TypeError, ValueError):
                    raise ValueError('Source gap disclosure date is invalid') from None
                row['public_at_cutoff'] = bool(cutoff and published <= date.fromisoformat(cutoff))
            else:
                row['public_at_cutoff'] = False
            if identifier in catalog and catalog[identifier] != row:
                raise ValueError('Conflicting records share a frozen source gap identity')
            catalog[identifier] = row
    return sorted(catalog.values(), key=lambda row: row['id'])


def source_gap_summary(rows):
    from collections import Counter
    return {'records': len(rows), 'digest': digest(rows),
            'by_reason': dict(Counter(str(row.get('gap_kind') or 'unresolved') for row in rows)),
            'eligible_for_verification': False}
