"""Disclosure metadata comes from a frozen corpus index, never report prose."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import json


def canonical_report_resources(records) -> list[dict]:
    """One immutable byte identity may have multiple lawful disclosure events.

    Keep all registered paths/dates visible and use the earliest known public
    event for reading those identical bytes. Unknown dates never invent one.
    """
    grouped = {}
    for record in records:
        grouped.setdefault(record['sha256'], []).append(record)
    resources = []
    for sha, rows in sorted(grouped.items()):
        companies = {row.get('company') for row in rows if row.get('company')}
        if len(companies) > 1:
            raise ValueError('同一披露文件的登记发行人冲突')
        selected = min(rows, key=lambda row: (not bool(row.get('publish_date')),
                                              row.get('publish_date') or '', row['path']))
        events = [event for row in rows for event in (row.get('disclosure_events') or [{'path': row['path'],
                              'publish_date': row.get('publish_date') or '',
                              'period': row.get('period') or ''}])]
        resources.append({'id': 'report-'+sha[:20], **selected,
                          'disclosure_events': sorted(events, key=lambda row: (row['publish_date'], row['path']))})
    return resources


def indexed_source_metadata(source: Path, company: str, **control) -> dict[str, dict]:
    from .parser import _indexed_files, _file_hash
    selected = Path(source).resolve()
    # A fact-index file selects its material directory. Treating it like a
    # selected PDF would silently filter out every registered source row.
    material = selected.parent if selected.is_file() and selected.suffix.lower() in {'.json', '.jsonl'} else selected
    indexed = _indexed_files(material, company, **control)
    if indexed is None:
        return {}
    base = selected.parent if selected.is_file() else selected
    index = next(parent/'index.csv' for parent in [base, *base.parents] if (parent/'index.csv').is_file())
    index_sha = _file_hash(index, **control)
    source_manifest = index.parent / 'material_manifest.json'
    declared_sources = {}
    if source_manifest.is_file():
        from .config import validate_path
        source_manifest = validate_path(source_manifest, allowed_roots=[str(index.parent)],
                                        must_exist=True, kind='file', follow_symlinks=False)
        if source_manifest.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('来源声明清单超过允许大小')
        manifest_data = json.loads(source_manifest.read_text(encoding='utf-8'))
        for document in manifest_data.get('documents', []):
            path = validate_path(index.parent / document['file'], allowed_roots=[str(index.parent)],
                                 must_exist=True, kind='file', follow_symlinks=False)
            if str(path) in declared_sources:
                raise ValueError('来源声明清单含重复文件')
            declared_sources[str(path)] = document
        manifest_sha = _file_hash(source_manifest, **control)
    records = {}
    for path, issuer, period, published in indexed:
        if published:
            try:
                if date.fromisoformat(published).isoformat() != published:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise ValueError('index.csv 披露日期必须是有效 YYYY-MM-DD；不能从正文推断') from exc
        record = {'path': str(path), 'sha256': _file_hash(path, **control), 'company': issuer,
                  'period': period, 'publish_date': published,
                  'publish_date_basis': 'frozen_corpus_index', 'index_path': str(index), 'index_sha256': index_sha}
        declared = declared_sources.get(str(path))
        if declared:
            if declared.get('sha256') != record['sha256'] or declared.get('date') != published:
                raise ValueError('来源声明与冻结索引的字节或披露日期冲突')
            record['source_provenance'] = {key: declared[key] for key in (
                'source_kind', 'provenance', 'review_status', 'url', 'official_publication_proof') if key in declared}
            record['source_manifest_path'] = str(source_manifest)
            record['source_manifest_sha256'] = manifest_sha
        previous = records.get(str(path))
        if previous and previous != record:
            comparable = lambda row: {key: value for key, value in row.items()
                                      if key not in {'publish_date', 'disclosure_events'}}
            if comparable(previous) != comparable(record):
                raise ValueError('index.csv 同一来源的披露元数据冲突')
            record = canonical_report_resources([previous, record])[0]
            record.pop('id')
        records[str(path)] = record
    return records
