"""Small model views of host-owned binding evidence.

This projection never replaces the frozen proof. The dispatcher still uses
the full proof to authorize every binding, and roles can read that proof via
``mj_read_context`` using their already authorized mention ID.
"""
from __future__ import annotations

from copy import deepcopy
import re

from .audit import digest


def compact_binding_metadata(dimensions: dict, *, redact=None) -> dict:
    """Project semantic fields and evidence summaries without numeric cells.

    Detailed header geometry, whole-table cohorts and classification excerpts
    repeat the same source information in every occurrence. Keep only their
    authority, source identity and hash in a paginated list; preserve the
    original tree unchanged for source checks and on-demand reads. Small
    metadata with no such trees retains its existing public shape.
    """
    large = ('dimension_source', 'scope_proof', 'source_table_reference', 'source_threshold_reference',
             'classification_basis', 'source_period_list')
    if not any(dimensions.get(key) for key in large):
        return redact(dimensions) if redact else deepcopy(dimensions)

    fields = {
        'schema_version', 'company', 'account', 'period', 'scope', 'relation',
        'base_period', 'dimension_kind', 'dimension_id', 'currency', 'definition_id',
        'source_text_sha256', 'source_start', 'source_end', 'source_numeric_start',
        'provenance', 'scope_provenance', 'explicit', 'evaluation_scope',
        'company_provenance', 'source_company_subjects', 'company_source_explicit',
        'source_company_conflict', 'source_company_ambiguous',
        'source_subject_noncorporate_fragment', 'scope_header',
        'scope_definition_id', 'scope_definition_label', 'scope_definition_basis',
        'scope_definition_source', 'scope_declaration_resource_id',
        'scope_declaration_sha256', 'period_comparison_explicit',
        'percentage_point_direction_conflict', 'hedge_kind', 'hedge_rule_basis',
        'source_cagr_period',
        'source_provenance', 'scope_assumption', 'validation_policy', 'smart_segment',
        'smart_entity', 'smart_notes', 'smart_dimensions',
    }
    view = {key: deepcopy(value) for key, value in dimensions.items() if key in fields}
    view.update(metadata_view='compact-binding.v1', source_metadata_sha256=digest(dimensions))

    dimension = dimensions.get('dimension_source')
    if isinstance(dimension, dict):
        cell = dimension.get('cell')
        if isinstance(cell, dict):
            # Position metadata is not a candidate amount. Do not retain the
            # cell's text, nor duplicate captions/labels already in context.
            view['source_geometry'] = {key: deepcopy(cell[key]) for key in
                ('start', 'end', 'page', 'bbox') if key in cell}

    reference = dimensions.get('source_table_reference')
    if isinstance(reference, dict):
        keys = ('schema_version', 'sha256', 'source_doc', 'report_id',
                'publish_date', 'cutoff', 'source_table_ids',
                'source_dimension_id', 'source_row_index',
                'dimension_mapping_basis', 'accounting_scope')
        summary = {key: deepcopy(reference[key]) for key in keys if key in reference}
        citation = reference.get('citation')
        if isinstance(citation, dict):
            summary['citation'] = citation.get('text', '')
        view['source_table_reference'] = summary

    threshold = dimensions.get('source_threshold_reference')
    if isinstance(threshold, dict):
        view['source_threshold_reference'] = {key:deepcopy(threshold[key]) for key in
            ('schema_version','sha256','source_doc','publish_date','cutoff','source_gap_id',
             'period','dimension_kind','dimension_id','account','source_operator','eligible_as_point_fact')
            if key in threshold}

    scope = dimensions.get('scope_proof')
    if isinstance(scope, dict):
        keys = ('schema_version', 'scope', 'kind', 'sha256', 'entity_id',
                'source_rule_id', 'professional_signoff_status')
        summary = {key: deepcopy(scope[key]) for key in keys if key in scope}
        structure_labels = []
        for table in scope.get('tables', []):
            structure = table.get('structure') if isinstance(table, dict) else None
            if isinstance(structure, dict):
                labels = {key: structure[key].get('text', '') for key in
                    ('parent_caption', 'minority_caption') if isinstance(structure.get(key), dict)}
                if labels:
                    structure_labels.append(labels)
        if structure_labels:
            summary['structure_labels'] = structure_labels
        for key in ('parent_caption', 'minority_caption'):
            if isinstance(scope.get(key), dict):
                summary[key] = scope[key].get('text', '')
        provenance = scope.get('summary')
        if isinstance(provenance, dict):
            summary['summary'] = {key: provenance[key].get('text', '') for key in
                ('heading', 'citation') if isinstance(provenance.get(key), dict)}
        view['scope_proof'] = summary

    period = dimensions.get('source_period_list')
    if isinstance(period, dict):
        view['source_period_list'] = {key: deepcopy(period[key]) for key in
            ('source', 'first_year', 'last_year', 'period_kind', 'occurrence_index',
             'source_unit', 'ambiguous') if key in period}
    currency = dimensions.get('source_currency')
    if isinstance(currency, dict):
        view['source_currency'] = {key: deepcopy(currency[key]) for key in
            ('currency', 'source', 'currency_texts', 'conflict') if key in currency}
    if not redact:
        return view
    blinded = redact(view)
    # A hash or a registered resource identifier is not a financial number.
    # General text masking otherwise corrupts it and can expand a 64-byte
    # hash into hundreds of characters. Restore only strict host formats,
    # never raw text, arbitrary IDs, numeric strings or financial labels.
    formats = {
        'source_text_sha256': r'[a-f0-9]{64}',
        'source_metadata_sha256': r'[a-f0-9]{64}',
        'scope_declaration_sha256': r'[a-f0-9]{64}',
        'sha256': r'[a-f0-9]{64}',
        'source_doc': r'[a-f0-9]{12}',
        'report_id': r'report-[a-f0-9]{20}',
        'entity_id': r'financial-summary-[a-f0-9]{20}',
    }
    pairs = [(view, blinded)]
    for key in ('source_table_reference', 'source_threshold_reference', 'scope_proof'):
        if isinstance(view.get(key), dict):
            pairs.append((view[key], blinded[key]))
    for original, output in pairs:
        for key, pattern in formats.items():
            value = original.get(key)
            if isinstance(value, str) and re.fullmatch(pattern, value):
                output[key] = value
        identifiers = original.get('source_table_ids')
        if (isinstance(identifiers, list) and identifiers
                and all(isinstance(identifier, str) and
                        re.fullmatch(r'source-table-[a-f0-9]{24}', identifier)
                        for identifier in identifiers)):
            output['source_table_ids'] = list(identifiers)
        version = original.get('schema_version')
        if version in {'source-dimensions.v1', 'source-table-reference.v1',
                       'source-financial-summary-structure.v1', 'source-table-structure.v1'}:
            output['schema_version'] = version
        if original.get('source_rule_id') == 'SOURCE-summary-consolidated-structure.v1':
            output['source_rule_id'] = original['source_rule_id']
    if view.get('scope_definition_id') == 'NP_PARENT-consolidated.v1':
        blinded['scope_definition_id'] = view['scope_definition_id']
    blinded['metadata_view'] = 'compact-binding.v1'
    return blinded
