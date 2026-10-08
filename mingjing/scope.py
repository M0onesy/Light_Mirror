"""Source scope evidence and host-created user clarifications.

Pure standard-library code shared by the financial tools and Decimal kernel.
Neither parser defaults nor an Agent's proposed binding prove source scope.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import replace


SCOPES = ('', '合并', '母公司')
_FINANCIAL_CONTEXT = (r'财务报表|报表|口径|资产负债表|利润表|现金流量表|'
                      r'营业总收入|营业收入|营收|营业成本|净利润|经营现金流|'
                      r'20\d{2}年|报告期')
_PARENT_LABEL_CARDS = {
    '归母净利润': 'CAL-014', '归属母公司净利润': 'CAL-014', '归属于母公司净利润': 'CAL-014',
    '归属于母公司所有者的净利润': 'CAL-015', '归属于母公司股东的净利润': 'CAL-016',
    '归属于上市公司股东的净利润': 'CAL-017', '归属于挂牌公司股东的净利润': 'CAL-018',
}
_GROWTH_ROW = re.compile(r'(?:YOY|同比|增长率)[（(]?[%％]?[）)]?', re.I)


def _target_prefix(claim) -> str | None:
    position = claim.source_numeric_start
    if (type(position) is not int or not claim.numeric_text or position < 0 or
            claim.text[position:position + len(claim.numeric_text)] != claim.numeric_text):
        return None
    return claim.text[:position]


def _validated_account_lineage(claim, source: dict) -> str | None:
    """Validate the host's preceding label excerpt; account_hint is not proof."""
    lineage = source.get('account_lineage')
    if (not isinstance(lineage, dict) or lineage.get('schema_version') != 'source-table-account-lineage.v1'
            or lineage.get('kind') != 'preceding_primary_row'
            or not _GROWTH_ROW.fullmatch(re.sub(r'\s+', '', str(source.get('row_label', ''))))):
        return None
    prefix = _target_prefix(claim)
    label = lineage.get('label_text')
    spans = lineage.get('label_spans')
    left, right = lineage.get('label_start_in_context'), lineage.get('label_end_in_context')
    row_start = lineage.get('row_start')
    if (prefix is None or not isinstance(label, str) or not label or
            type(left) is not int or type(right) is not int or left != 0 or right != len(label)
            or prefix[left:right] != label or not prefix[right:].startswith('\n')
            or hashlib.sha256(label.encode('utf-8')).hexdigest() != lineage.get('label_sha256')
            or hashlib.sha256(str(source.get('title', '')).encode('utf-8')).hexdigest() != lineage.get('table_title_sha256')
            or not isinstance(spans, list) or not spans or type(row_start) is not int):
        return None
    source_spans = source.get('source_spans', [])
    prior_end = -1
    for span in spans:
        if (not isinstance(span, dict) or type(span.get('start')) is not int or type(span.get('end')) is not int
                or not isinstance(span.get('text'), str) or not span['text']
                or not prior_end <= span['start'] < span['end'] <= row_start <= claim.start
                or span['end'] - span['start'] != len(span['text'])
                or {'start': span['start'], 'end': span['end']} not in source_spans):
            return None
        prior_end = span['end']
    if '\n'.join(span['text'] for span in spans) != label:
        return None
    # The label and current YOY caption are both original context excerpts.
    # Prior row amounts are deliberately absent, preserving blind binding.
    return label


def _parent_account_definition(claim, source: dict, table: bool) -> dict | None:
    from .mentions import ACCOUNT_LABELS, _account_before
    prefix = _target_prefix(claim)
    if prefix is None:
        return None
    growth_row = table and _GROWTH_ROW.fullmatch(re.sub(r'\s+', '', str(source.get('row_label', ''))))
    if growth_row:
        prefix = _validated_account_lineage(claim, source)
        if prefix is None:
            return None
    if _account_before(prefix, len(prefix)) != 'NP_PARENT':
        return None
    compact = re.sub(r'\s+', '', prefix)
    # Only the latest complete financial label may define this occurrence.
    # A later ordinary revenue/profit label prevents scope propagation.
    hits = [(m.start(), m.end(), label) for label, account in ACCOUNT_LABELS if account == 'NP_PARENT'
            for m in re.finditer(re.escape(label), compact) if label in _PARENT_LABEL_CARDS]
    if not hits:
        return None
    _, _, label = max(hits, key=lambda value: (value[1], value[1]-value[0]))
    return {'scope_definition_id': 'NP_PARENT-consolidated.v1',
            'scope_definition_label': label,
            'scope_definition_basis': {'source_id': 'CAS33', 'clause_id': 'CAS33-三十六',
                                       'caliber_id': _PARENT_LABEL_CARDS[label],
                                       'professional_signoff_status': 'pending'},
            'scope_definition_source': 'validated_preceding_table_label' if growth_row else 'source_financial_label'}


def validate_scope_declaration(value) -> str:
    if not isinstance(value, str) or value not in SCOPES:
        raise ValueError('scope_declaration 只接受空值、合并或母公司；空值表示仅按原文')
    return value


def create_scope_clarification(scope_declaration: str, *, draft_sha256: str) -> dict | None:
    """Return a deterministic user material; callers archive it before locking."""
    scope = validate_scope_declaration(scope_declaration)
    if not scope:
        return None
    if not isinstance(draft_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', draft_sha256):
        raise ValueError('口径澄清必须关联原草稿的 SHA-256')
    resource = {'schema_version': 'scope-clarification.v1', 'provenance': 'user_supplied_scope',
                'scope': scope, 'draft_sha256': draft_sha256}
    canonical = json.dumps(resource, ensure_ascii=False, sort_keys=True).encode('utf-8')
    resource['resource_id'] = 'scope-clarification-' + hashlib.sha256(canonical).hexdigest()[:20]
    return resource


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def scope_table_id(claim) -> str:
    """Stable draft-table identity: source caption and layout, never amounts.

    This is a draft table ID, intentionally distinct from an external fact's
    source_table_id. A clarification cannot select an arbitrary source table.
    """
    context = claim.table_context if isinstance(claim.table_context, dict) else {}
    title = context.get('title_source')
    if (not isinstance(title, dict) or type(title.get('start')) is not int
            or type(title.get('end')) is not int or not isinstance(title.get('text'), str)
            or not 0 <= title['start'] < title['end']
            or title['end'] - title['start'] != len(title['text'])):
        return ''
    identity = {'caption': {key: title[key] for key in ('start', 'end', 'text')},
                'layout_source': context.get('layout_source', ''),
                'layout_lane': context.get('layout_lane')}
    return 'draft-table-' + _digest(identity)[:24]


def validate_scope_clarifications(specs) -> list[dict]:
    """Validate UI input; fields intersect, values within a field are alternatives."""
    if not isinstance(specs, list) or len(specs) > 256:
        raise ValueError('scope_clarifications 必须为最多256组的列表')
    output = []
    for spec in specs:
        if not isinstance(spec, dict) or set(spec) != {'scope', 'selector'}:
            raise ValueError('分组口径仅接受 scope 和 selector，不接受金额、公式或判定')
        scope = validate_scope_declaration(spec['scope'])
        selector = spec['selector']
        if not scope or not isinstance(selector, dict) or not selector or set(selector) - {'claim_ids', 'accounts', 'table_ids'}:
            raise ValueError('分组口径须有非空口径和 claim_ids/accounts/table_ids 选择器')
        normalized = {}
        for field, values in selector.items():
            if (not isinstance(values, list) or not 1 <= len(values) <= 10000
                    or any(not isinstance(value, str) or not value or len(value) > 200 for value in values)
                    or len(set(values)) != len(values)):
                raise ValueError('分组口径选择器须为非空、无重复的字符串列表')
            pattern = {'claim_ids': r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}',
                       'accounts': r'[A-Z][A-Z0-9_]{0,79}',
                       'table_ids': r'draft-table-[a-f0-9]{24}'}[field]
            if any(not re.fullmatch(pattern, value) for value in values):
                raise ValueError('分组口径选择器包含非法资源标识')
            normalized[field] = sorted(values)
        output.append({'scope': scope, 'selector': normalized})
    return output


def _clarification_target(claim) -> dict:
    """Freeze original occurrence identity without persisting a candidate amount."""
    if (not isinstance(claim.claim_id, str) or type(claim.start) is not int
            or type(claim.end) is not int or not 0 <= claim.start < claim.end
            or not isinstance(claim.text, str) or not isinstance(claim.numeric_text, str)
            or claim.source_numeric_start is not None and type(claim.source_numeric_start) is not int):
        raise ValueError('分组口径目标缺少有效原文位置')
    source = claim.table_context if isinstance(claim.table_context, dict) else {}
    return {'claim_id': claim.claim_id, 'start': claim.start, 'end': claim.end,
            'source_numeric_start': claim.source_numeric_start,
            'text_sha256': hashlib.sha256(claim.text.encode('utf-8')).hexdigest(),
            'numeric_sha256': hashlib.sha256(claim.numeric_text.encode('utf-8')).hexdigest(),
            'candidate_sha256': _digest(claim.candidate_value),
            'company': claim.company, 'account': claim.account, 'period': claim.period,
            'relation': claim.relation, 'base_period': claim.base_period,
            'table_id': scope_table_id(claim),
            'dimension_kind': source.get('dimension_kind', 'total'),
            'dimension_id': source.get('dimension_id', '')}


def _grouped_root(resource) -> dict:
    return {'schema_version': 'scope-clarifications.v2', 'provenance': 'user_supplied_scope',
            'draft_sha256': resource['draft_sha256'],
            'groups': [{'scope': group['scope'], 'selector': group['selector'],
                        'target_hashes': [_digest(target) for target in group['targets']]}
                       for group in resource['groups']]}


def create_grouped_scope_clarifications(claims: list, specs: list, *, draft_sha256: str) -> dict | None:
    """Resolve host-validated selectors once and freeze every matched occurrence.

    Selection does not inspect candidate values. Hashes protect values after
    selection. The caller must archive this resource in the new task lock.
    """
    specs = validate_scope_clarifications(specs)
    if not specs:
        return None
    if not isinstance(draft_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', draft_sha256):
        raise ValueError('分组口径澄清必须关联原草稿的 SHA-256')
    if len({claim.claim_id for claim in claims}) != len(claims):
        raise ValueError('分组口径目标原文标识重复')
    available = {'claim_ids': {claim.claim_id for claim in claims},
                 'accounts': {claim.account for claim in claims},
                 'table_ids': {scope_table_id(claim) for claim in claims} - {''}}
    groups, assigned = [], {}
    for spec in specs:
        selector = spec['selector']
        if any(set(values) - available[field] for field, values in selector.items()):
            raise ValueError('分组口径选择器包含未登记资源或指标')
        selected = [claim for claim in claims if all(
            {'claim_ids': claim.claim_id, 'accounts': claim.account,
             'table_ids': scope_table_id(claim)}[field] in values for field, values in selector.items())]
        if not selected:
            raise ValueError('分组口径选择器没有共同匹配的原文条目')
        for claim in selected:
            if claim.claim_id in assigned and assigned[claim.claim_id] != spec['scope']:
                raise ValueError('分组口径澄清目标重叠且口径冲突')
            assigned[claim.claim_id] = spec['scope']
        groups.append({**spec, 'targets': [_clarification_target(claim) for claim in selected]})
    resource = {'schema_version': 'scope-clarifications.v2', 'provenance': 'user_supplied_scope',
                'draft_sha256': draft_sha256, 'groups': groups}
    resource['resource_id'] = 'scope-clarification-' + _digest(_grouped_root(resource))[:20]
    # Reject source contradictions before a resource is archived. Neither the
    # inputs nor old frozen dimensions are mutated by this preflight.
    apply_scope_clarification(claims, scope_clarification_metadata(resource, _digest(resource)),
                              draft_sha256=draft_sha256)
    return resource


def _validated_grouped_resource(resource: dict) -> dict:
    if (not isinstance(resource, dict)
            or set(resource) != {'schema_version', 'provenance', 'draft_sha256', 'groups', 'resource_id'}
            or resource.get('schema_version') != 'scope-clarifications.v2'
            or resource.get('provenance') != 'user_supplied_scope'
            or not isinstance(resource.get('draft_sha256'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', resource['draft_sha256'])
            or not isinstance(resource.get('groups'), list) or not 1 <= len(resource['groups']) <= 256):
        raise ValueError('分组口径澄清缺少可信冻结资源')
    for group in resource['groups']:
        if (not isinstance(group, dict) or set(group) != {'scope', 'selector', 'targets'}
                or not isinstance(group.get('targets'), list) or not group['targets']):
            raise ValueError('分组口径澄清缺少冻结目标')
        validate_scope_clarifications([{'scope': group['scope'], 'selector': group['selector']}])
        if any(not _valid_clarification_target(target) for target in group['targets']):
            raise ValueError('分组口径澄清目标结构无效')
    root = _grouped_root(resource)
    if resource.get('resource_id') != 'scope-clarification-' + _digest(root)[:20]:
        raise ValueError('分组口径澄清资源哈希不一致')
    return root


def _valid_clarification_target(target) -> bool:
    required = {'claim_id', 'start', 'end', 'source_numeric_start', 'text_sha256',
                'numeric_sha256', 'candidate_sha256', 'company', 'account', 'period',
                'relation', 'base_period', 'table_id', 'dimension_kind', 'dimension_id'}
    return bool(isinstance(target, dict) and set(target) == required
                and all(isinstance(target[field], str) for field in required -
                        {'start', 'end', 'source_numeric_start', 'base_period'})
                and type(target['start']) is int and type(target['end']) is int
                and 0 <= target['start'] < target['end']
                and (target['source_numeric_start'] is None
                     or type(target['source_numeric_start']) is int and target['source_numeric_start'] >= 0)
                and (target['base_period'] is None or isinstance(target['base_period'], str))
                and all(re.fullmatch(r'[a-f0-9]{64}', target[field])
                        for field in ('text_sha256', 'numeric_sha256', 'candidate_sha256')))


def _validate_target_declaration(declaration, claim) -> bool:
    """Check a compact membership certificate against the resource identity."""
    if (not isinstance(declaration, dict)
            or set(declaration) != {'schema_version', 'scope', 'provenance', 'resource_id', 'sha256',
                                    'draft_sha256', 'target', 'resource_binding', 'group_index'}
            or declaration.get('schema_version') != 'scope-clarification-target.v2'
            or declaration.get('provenance') != 'user_supplied_scope'
            or declaration.get('scope') not in SCOPES[1:]
            or not isinstance(declaration.get('sha256'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', declaration['sha256'])
            or not _valid_clarification_target(declaration.get('target'))
            or declaration['target'] != _clarification_target(claim)):
        return False
    root = declaration.get('resource_binding')
    if (not isinstance(root, dict) or set(root) != {'schema_version', 'provenance', 'draft_sha256', 'groups'}
            or root['schema_version'] != 'scope-clarifications.v2' or root['provenance'] != 'user_supplied_scope'
            or root['draft_sha256'] != declaration['draft_sha256']
            or not isinstance(root['draft_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', root['draft_sha256'])
            or not isinstance(root['groups'], list) or not 1 <= len(root['groups']) <= 256
            or type(declaration.get('group_index')) is not int
            or not 0 <= declaration['group_index'] < len(root['groups'])
            or declaration['resource_id'] != 'scope-clarification-' + _digest(root)[:20]):
        return False
    for group in root['groups']:
        if (not isinstance(group, dict) or set(group) != {'scope', 'selector', 'target_hashes'}
                or not isinstance(group['target_hashes'], list) or not group['target_hashes']
                or any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value)
                       for value in group['target_hashes'])):
            return False
        try:
            validate_scope_clarifications([{'scope': group['scope'], 'selector': group['selector']}])
        except ValueError:
            return False
    selected = root['groups'][declaration['group_index']]
    target = declaration['target']
    return bool(selected['scope'] == declaration['scope']
                and _digest(target) in selected['target_hashes']
                and all({'claim_ids': target['claim_id'], 'accounts': target['account'],
                         'table_ids': target['table_id']}[field] in values
                        for field, values in selected['selector'].items()))


def scope_clarification_metadata(resource: dict, sha256: str) -> dict:
    if isinstance(resource, dict) and resource.get('schema_version') == 'scope-clarifications.v2':
        _validated_grouped_resource(resource)
        if not isinstance(sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', sha256):
            raise ValueError('分组口径澄清缺少归档文件 SHA-256')
        return {**deepcopy(resource), 'sha256': sha256}
    scope = validate_scope_declaration(resource.get('scope'))
    if (not scope or resource.get('provenance') != 'user_supplied_scope'
            or resource.get('schema_version') != 'scope-clarification.v1'
            or not re.fullmatch(r'scope-clarification-[0-9a-f]{20}', str(resource.get('resource_id', '')))
            or not re.fullmatch(r'[0-9a-f]{64}', str(sha256))):
        raise ValueError('口径澄清缺少可信材料标识或哈希')
    return {'scope': scope, 'provenance': 'user_supplied_scope',
            'resource_id': resource['resource_id'], 'sha256': sha256}


def _source_scopes(text: str) -> tuple[set[str], set[str]]:
    text = re.sub(r'\s+', '', text)
    scopes, negated = set(), set()
    if re.search(r'(?:合并(?:及|与|和|/|、)母公司|母公司(?:及|与|和|/|、)合并)(?:' + _FINANCIAL_CONTEXT + ')', text):
        scopes.update(('合并', '母公司'))
    for scope, pattern in (('合并', r'合并'), ('母公司', r'(?<!归属)(?<!属于)母公司')):
        for match in re.finditer(pattern + '(?:' + _FINANCIAL_CONTEXT + ')', text):
            if re.search(r'(?:没有|并非|未|非|不|无)(?:再|能|曾|按|用|采用|使用|是|属于)*$', text[:match.start()]):
                negated.add(scope)
            else:
                scopes.add(scope)
    return scopes, negated


def source_scope_dimensions(claim) -> dict:
    """Expose source dimensions, with an explicit provenance for clarification."""
    source = claim.table_context if isinstance(claim.table_context, dict) else {}
    table = source.get('period_source') == 'source_header'
    text = str(source.get('title', '')) + '；' + claim.text if table else claim.text
    scopes, negated = _source_scopes(text)
    structure = _validated_structure_scope(claim, source) if table else None
    if structure:
        scopes.add('合并')
    conflict = bool(scopes & negated)
    definition = _parent_account_definition(claim, source, table)
    definition_conflict = bool(definition and (conflict or len(scopes) > 1 or
                               '母公司' in scopes or '合并' in negated))
    if definition and not definition_conflict:
        scopes.add('合并')
    if definition_conflict:
        conflict = True

    result = {'scope_header': next(iter(scopes)) if len(scopes) == 1 and not conflict else '',
              'provenance': 'deterministic_table_headers' if table else 'source_text_only'}
    if structure:
        result['scope_proof'] = structure
        if len(scopes) == 1 and not conflict:
            result['provenance'] = 'source_table_consolidated_structure'
    if definition:
        result.update(definition)
        if not definition_conflict and not _source_scopes(text)[0]:
            result['provenance'] = 'source_account_definition'
    if conflict:
        result['source_scope_conflict'] = True
    if table:
        result.update(period_header=source.get('period_hint', ''), account_header=source.get('account_hint', ''),
                      unit_header=source.get('unit_hint', ''), relation_header=source.get('relation_hint', ''))
    if len(scopes) > 1:
        result['source_scope_ambiguous'] = True
    from .source_table_reference import TABLE_SCOPE, validated_table_reference
    from .source_quarter_quotation import validated_threshold_reference
    reference = validated_table_reference(claim)
    threshold_reference = validated_threshold_reference(claim)
    # A cited table is a source-addressed transcription check. A general or
    # grouped accounting assumption cannot erase its admitted source identity.
    # Explicit contradictory original headers still block this path above.
    source_table_admitted = bool((reference or threshold_reference) and not scopes and not conflict
                                 and not result.get('source_scope_ambiguous'))
    declaration = source.get('scope_declaration')
    if isinstance(declaration, dict):
        scope = declaration.get('scope')
        grouped = ('resource_binding' in declaration or 'target' in declaration
                   or str(declaration.get('schema_version', '')).startswith('scope-clarification-target.'))
        try:
            valid = (_validate_target_declaration(declaration, claim) if grouped else
                     scope in SCOPES[1:] and declaration.get('provenance') == 'user_supplied_scope'
                     and re.fullmatch(r'scope-clarification-[0-9a-f]{20}', str(declaration.get('resource_id', '')))
                     and re.fullmatch(r'[0-9a-f]{64}', str(declaration.get('sha256', ''))))
        except (ValueError, TypeError):
            valid = False
        if grouped and not valid:
            result.update(scope_declaration_conflict=True, scope_header='',
                          scope_declaration_invalid=True)
        if valid:
            declaration_conflict = conflict or len(scopes) > 1 or bool(scopes and scope not in scopes) or scope in negated
            result['scope_declaration_conflict'] = declaration_conflict
            if not scopes and not declaration_conflict and not source_table_admitted:
                result.update(scope_header=scope, provenance='user_supplied_scope',
                              scope_declaration_resource_id=declaration['resource_id'],
                              scope_declaration_sha256=declaration['sha256'])
                if grouped:
                    result.update(scope_declaration_draft_sha256=declaration['draft_sha256'],
                                  scope_declaration_target_sha256=_digest(declaration['target']),
                                  conditional_verification=True)
    if source_table_admitted and not result.get('scope_declaration_invalid'):
        result.update(scope_header=TABLE_SCOPE,
                      provenance='explicit_disclosed_table_reference' if reference else 'explicit_disclosed_threshold_reference')
        result['source_table_reference' if reference else 'source_threshold_reference'] = reference or threshold_reference
        if declaration:
            result['scope_declaration_ignored_for_source_reference'] = True
    return result


def _validated_structure_scope(claim, source: dict) -> dict | None:
    proof = source.get('scope_proof')
    if isinstance(proof,dict) and proof.get('schema_version')=='source-financial-summary-structure.v1':
        return _validated_summary_structure_scope(claim,source,proof)
    if not isinstance(proof, dict) or proof.get('schema_version') != 'source-table-structure.v1':
        return None
    value = {key: item for key, item in proof.items() if key != 'sha256'}
    if (proof.get('scope') != '合并' or proof.get('kind') not in {'balance_sheet', 'income_statement'}
            or hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest() != proof.get('sha256')):
        return None
    title, parent, minority = (proof.get(key) for key in ('title', 'parent_caption', 'minority_caption'))
    left, right = proof.get('start'), proof.get('end')
    if (type(left) is not int or type(right) is not int or not left <= claim.start < claim.end <= right
            or title != source.get('title_source') or not isinstance(title, dict)
            or '母公司' in title.get('text', '') or '现金流量表' in title.get('text', '')
            or proof.get('layout_source') != source.get('layout_source')):
        return None
    patterns = {'balance_sheet': (r'归属(?:于)?母公司(?:所有者|股东)权益(?:合计)?', r'少数股东权益(?:合计)?'),
                'income_statement': (r'归属(?:于)?母公司(?:所有者|股东)?(?:的)?净利润', r'少数股东损益')}
    for ref, pattern in zip((parent, minority), patterns[proof['kind']]):
        if (not isinstance(ref, dict) or type(ref.get('start')) is not int or type(ref.get('end')) is not int
                or not left <= ref['start'] < ref['end'] <= right or not isinstance(ref.get('text'), str)
                or ref['end']-ref['start'] != len(ref['text'])
                or not re.fullmatch(pattern, re.sub(r'\s+', '', ref['text']))):
            return None
    if parent['start'] == minority['start']:
        return None
    return dict(proof)


def _validated_summary_structure_scope(claim,source,proof):
    from .table_mentions import _header
    value={key:item for key,item in proof.items() if key not in {'sha256','entity_id'}}
    if (proof.get('scope')!='合并' or proof.get('kind')!='bounded_combined_financial_summary'
            or proof.get('source_rule_id')!='SOURCE-summary-consolidated-structure.v1'
            or hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()!=proof.get('sha256')
            or proof.get('entity_id')!='financial-summary-'+proof['sha256'][:20]
            or proof.get('professional_signoff_status')!='pending'
            or source.get('layout_source')!='pdf_text_line_lanes'):
        return None
    summary=proof.get('summary')
    if not isinstance(summary,dict) or summary!=source.get('summary_source'):
        return None
    heading,citation=summary.get('heading'),summary.get('citation')
    def ref_valid(ref):
        if (not isinstance(ref,dict) or type(ref.get('start')) is not int or type(ref.get('end')) is not int
                or not isinstance(ref.get('text'),str) or ref['end']-ref['start']!=len(ref['text'])):
            return False
        box=ref.get('bbox')
        if not isinstance(box,(list,tuple)) or len(box)!=4:
            return False
        import math
        return all(type(number) in {int,float} and math.isfinite(number) for number in box) and box[0]<box[2] and box[1]<box[3]
    if (not ref_valid(heading) or not ref_valid(citation)
            or not re.fullmatch(r'(?:附[:：]?)?财务预测摘要',re.sub(r'\s+','',heading['text']))
            or not re.match(r'^(?:数据|资料)来源[:：]',re.sub(r'\s+','',citation['text']))
            or heading['end']>=citation['start']):
        return None
    tables=proof.get('tables')
    expected={'balance_sheet':'资产负债表','income_statement':'利润表','cash_flow_statement':'现金流量表','financial_ratios':'主要财务比率'}
    if not isinstance(tables,list) or len(tables)!=4 or {table.get('kind') for table in tables if isinstance(table,dict)}!=set(expected):
        return None
    by_kind={table['kind']:table for table in tables}
    target=None
    for table in tables:
        title=table.get('title');left,right=table.get('start'),table.get('end')
        if (not ref_valid(title) or type(left) is not int or type(right) is not int
                or not heading['end']<=title['start']<left<=right<=citation['start']
                or not heading['bbox'][3]<title['bbox'][1]<citation['bbox'][1]
                or table.get('lane') not in {1,2}
                or not re.fullmatch(re.escape(expected[table['kind']])+r'(?:[（(](?:百万元|亿元|万元|元)[）)])?',re.sub(r'\s+','',title['text']))):
            return None
        headers=table.get('headers')
        if not isinstance(headers,list) or len(headers)<2:
            return None
        signature=[]
        for header in headers:
            if (not isinstance(header,dict) or type(header.get('start')) is not int or type(header.get('end')) is not int
                    or not isinstance(header.get('text'),str) or header['end']-header['start']!=len(header['text'])
                    or not title['end']<=header['start']<header['end']<=right or not _header(header['text'])):
                return None
            signature.append(list(_header(header['text'])))
        if signature!=table.get('period_signature') or signature!=proof.get('period_signature') or len({item[0] for item in signature})!=len(signature):
            return None
        if title==source.get('title_source'):
            target=table
    bs,ins,cf,ratios=(by_kind[name] for name in expected)
    if (target is None or target['headers']!=source.get('headers') or target['lane']!=source.get('layout_lane')
            or not target['start']<=claim.start<claim.end<=target['end']
            or {table['lane'] for table in tables}!={1,2}
            or bs['lane']!=cf['lane'] or ins['lane']!=ratios['lane'] or bs['lane']==ins['lane']
            or abs(bs['title']['bbox'][1]-ins['title']['bbox'][1])>4
            or abs(bs['title']['bbox'][0]-cf['title']['bbox'][0])>4
            or abs(ins['title']['bbox'][0]-ratios['title']['bbox'][0])>4
            or abs(bs['title']['bbox'][0]-ins['title']['bbox'][0])<120
            or bs['title']['bbox'][3]>=cf['title']['bbox'][1] or ins['title']['bbox'][3]>=ratios['title']['bbox'][1]):
        return None
    for table,kind in ((bs,'balance_sheet'),(ins,'income_statement')):
        structure=table.get('structure')
        if not isinstance(structure,dict) or structure.get('kind')!=kind:
            return None
        isolated=dict(source,scope_proof=structure,title_source=table['title'],title=table['title']['text'])
        # Existing proof validator also checks the target location. Select a
        # host source-addressed caption endpoint inside that constituent table.
        from dataclasses import replace
        anchored=replace(claim,start=table['start'],end=min(table['start']+1,table['end']))
        if _validated_structure_scope(anchored,isolated) is None:
            return None
    scopes,negated=_source_scopes(str(source.get('title',''))+'；'+claim.text)
    if '母公司' in scopes or '合并' in negated:
        return None
    return dict(proof)


def apply_scope_clarification(claims: list, declaration: dict | None, *, draft_sha256: str | None = None) -> list:
    """Attach host material without changing source prose, values or offsets."""
    if declaration is None:
        return claims
    if not isinstance(declaration, dict):
        raise ValueError('口径澄清须为已归档的资源元数据')
    grouped = declaration.get('schema_version') == 'scope-clarifications.v2'
    if not grouped and ('groups' in declaration or declaration.get('schema_version')
                        not in {None, 'scope-clarification.v1'}):
        raise ValueError('未知口径澄清资源版本')
    declarations = {}
    if grouped:
        resource = {key: value for key, value in declaration.items() if key != 'sha256'}
        root = _validated_grouped_resource(resource)
        if not isinstance(draft_sha256, str) or draft_sha256 != resource['draft_sha256']:
            raise ValueError('分组口径澄清与当前草稿 SHA-256 不一致')
        metadata = scope_clarification_metadata(resource, declaration.get('sha256', ''))
        known = {claim.claim_id: claim for claim in claims}
        if len(known) != len(claims):
            raise ValueError('分组口径目标原文标识重复')
        for index, group in enumerate(metadata['groups']):
            for target in group['targets']:
                if target['claim_id'] not in known or target != _clarification_target(known[target['claim_id']]):
                    raise ValueError('分组口径冻结目标的原文、金额、位置或维度已改变')
                if target['claim_id'] in declarations and declarations[target['claim_id']]['scope'] != group['scope']:
                    raise ValueError('分组口径澄清目标重叠且口径冲突')
                current = {'schema_version': 'scope-clarification-target.v2',
                           'scope': group['scope'], 'provenance': 'user_supplied_scope',
                           'resource_id': resource['resource_id'], 'sha256': metadata['sha256'],
                           'draft_sha256': draft_sha256, 'target': deepcopy(target),
                           'resource_binding': deepcopy(root), 'group_index': index}
                if not _validate_target_declaration(current, known[target['claim_id']]):
                    raise ValueError('分组口径冻结目标与选择器不一致')
                declarations.setdefault(target['claim_id'], current)
    else:
        metadata = scope_clarification_metadata(
            dict(declaration, schema_version='scope-clarification.v1'), declaration.get('sha256', ''))
    output = []
    for claim in claims:
        if grouped and claim.claim_id not in declarations:
            output.append(claim)
            continue
        current = declarations[claim.claim_id] if grouped else metadata
        existing = claim.table_context.get('scope_declaration')
        if isinstance(existing, dict) and existing.get('scope') != current['scope']:
            raise ValueError('新口径澄清与已冻结口径澄清冲突；请新建明确修订的任务版本')
        context = {key: value for key, value in claim.table_context.items() if key != 'source_dimensions'}
        context['scope_declaration'] = current
        updated = replace(claim, table_context=context)
        dimensions = source_scope_dimensions(updated)
        if dimensions.get('scope_declaration_conflict'):
            raise ValueError('用户口径澄清与原文明示或双口径冲突；请修订材料后新建任务')
        scope = dimensions['scope_header']
        hints = claim.caliber_hint[1:] if claim.caliber_hint and claim.caliber_hint[0] in {'合并', '母公司', '未知', '来源表'} else claim.caliber_hint
        binding = claim.binding_status
        if (binding == 'unresolved' and claim.parse_status == 'parsed' and claim.company
                and claim.account and claim.account != 'UNKNOWN' and claim.period
                and claim.caliber_hint and claim.caliber_hint[0] == '未知'):
            binding = 'single'
        output.append(replace(updated, caliber_hint=(scope,) + hints, binding_status=binding))
    return output
