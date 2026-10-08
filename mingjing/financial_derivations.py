"""Authorised derived metrics, kept separate from disclosed raw Facts."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from decimal import Decimal
import hashlib
import json
import re

from .decimal_math import DecimalInterval, fact_interval
from .facts import FactStore
from .models import Fact, FactQueryOutcome
from .rules import execute_rule, execute_rule_interval, load_rule_cards
from .taxonomy import FINANCIAL_LABELS, SHARE_LABELS


FLOW_ACCOUNTS = frozenset({'REVENUE', 'COST', 'NP', 'NP_PARENT', 'NP_PARENT_ADJ', 'CFO', 'CFI', 'CFF'})
QUARTER_INPUTS = {'Q2': ('H1', 'Q1'), 'Q3': ('9M', 'H1'), 'Q4': ('Y', '9M')}
WEIGHTED_SHARE_LABELS = frozenset(SHARE_LABELS)
ORDINARY_PROFIT_LABELS = frozenset(label for label, code in FINANCIAL_LABELS.items()
                                 if code == 'NP_PARENT_ORDINARY')


# These are source layouts, not aliases for statutory OTHER_* items.  Every
# explicitly printed sibling is subtracted; a short layout cannot silently
# inherit the formula of a longer layout.
BROKER_COMPOSITIONS = {
    'BROKER_OTHER_CURRENT_ASSETS': {
        'rows': (('流动资产', 'CURRENT_ASSETS'), ('现金', 'ASSET_CASH_UNSPECIFIED'),
                 ('应收票据及应收账款', 'AR_NOTES'), ('其他应收款', 'OTHER_RECEIVABLES'),
                 ('预付账款', 'PREPAYMENTS'), ('存货', 'INVENTORY'),
                 ('其他流动资产', 'BROKER_OTHER_CURRENT_ASSETS')),
        'boundary': ('非流动资产', 'NONCURRENT_ASSETS'),
        'formula': 'CURRENT_ASSETS-CASH_SOURCE-AR_NOTES-OTHER_RECEIVABLES-PREPAYMENTS-INVENTORY',
    },
    'BROKER_OTHER_NONCURRENT_ASSETS': {
        'rows': (('非流动资产', 'NONCURRENT_ASSETS'), ('长期投资', 'INVESTMENTS_UNSPECIFIED'),
                 ('固定资产', 'PPE'), ('无形资产', 'INTANGIBLES'),
                 ('其他非流动资产', 'BROKER_OTHER_NONCURRENT_ASSETS')),
        'boundary': ('资产总计', 'ASSETS'),
        'formula': 'NONCURRENT_ASSETS-PPE-INTANGIBLES-INVESTMENTS_SOURCE',
    },
    'BROKER_OTHER_CURRENT_LIABILITIES': {
        'rows': (('流动负债', 'CURRENT_LIABILITIES'), ('短期借款', 'SHORT_TERM_BORROWING'),
                 ('应付票据及应付账款', 'AP_NOTES'),
                 ('其他流动负债', 'BROKER_OTHER_CURRENT_LIABILITIES')),
        'boundary': ('非流动负债', 'NONCURRENT_LIABILITIES'),
        'formula': 'CURRENT_LIABILITIES-SHORT_TERM_BORROWING-AP_NOTES',
    },
    'BROKER_OTHER_NONCURRENT_LIABILITIES': {
        'rows': (('非流动负债', 'NONCURRENT_LIABILITIES'), ('长期借款', 'LONG_TERM_BORROWING'),
                 ('其他非流动负债', 'BROKER_OTHER_NONCURRENT_LIABILITIES')),
        'boundary': ('负债合计', 'LIABILITIES'),
        'formula': 'NONCURRENT_LIABILITIES-LONG_TERM_BORROWING',
    },
}


def _source_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def _source_broker_identity(claim):
    context = claim.table_context or {}
    return {'company': claim.company, 'period': claim.period, 'start': claim.start,
            'end': claim.end, 'text_hash': hashlib.sha256(claim.text.encode('utf-8')).hexdigest(),
            'source_account': context.get('account_hint'),
            'title': context.get('title_source'), 'headers': context.get('headers'),
            'column_index': context.get('column_index'), 'lane': context.get('layout_lane'),
            'summary': context.get('summary_source'), 'row_label': context.get('row_label'),
            'spans': context.get('source_spans'), 'unit': context.get('source_unit_proof'),
            'layout_source': context.get('layout_source'), 'profile': context.get('table_profile'),
            'period_source': context.get('period_source')}


def _source_ref_valid(reference):
    return (isinstance(reference, dict)
            and type(reference.get('start')) is int and type(reference.get('end')) is int
            and 0 <= reference['start'] < reference['end']
            and isinstance(reference.get('text'), str)
            and len(reference['text']) == reference['end'] - reference['start'])


def broker_source_composition(claim, source_claims):
    """Freeze a complete, amount-free source partition of one broker column.

    Source cells identify the printed composition only.  Their candidate
    values never enter the formula; operands still come from FactStore.
    Unknown cash/investment definitions are deliberately preserved.
    """
    account = (claim.table_context or {}).get('account_hint')
    layout = BROKER_COMPOSITIONS.get(account)
    if layout is None:
        return None
    context = claim.table_context or {}
    proof = {'schema_version': 'source-broker-composition.v1', 'account': account,
             'source_identity': _source_broker_identity(claim), 'rows': [], 'blockers': [],
             'formula': layout['formula'], 'numeric_authority': 'external_facts_only',
             'cash_or_investment_definition_selected': False}

    def fail(reason, **extra):
        proof['blockers'].append({'code': 'X3', 'kind': 'source_composition',
                                  'reason': reason, **extra})

    title, headers, summary = (context.get('title_source'), context.get('headers'),
                                context.get('summary_source') or {})
    column = context.get('column_index')
    heading, citation = summary.get('heading'), summary.get('citation')
    unit = context.get('source_unit_proof') or {}
    if not (context.get('table_profile') == 'broker_compact'
            and context.get('layout_source') == 'pdf_text_line_lanes'
            and context.get('period_source') == 'source_header'
            and _source_ref_valid(title) and '资产负债表' in title['text']
            and isinstance(headers, list) and all(_source_ref_valid(x) for x in headers)
            and type(column) is int and 1 <= column <= len(headers)
            and type(context.get('layout_lane')) is int
            and _source_ref_valid(heading) and _source_ref_valid(citation)
            and summary.get('schema_version') == 'source-broker-summary.v1'
            and re.fullmatch(r'(?:附[:：]?)?(?:财务预测摘要|财务摘要和估值指标)', _caption(heading['text']))
            and citation['text'].strip().startswith('数据来源')
            and heading['end'] <= title['start'] < claim.start < claim.end <= citation['start']
            and unit.get('kind') == 'table_unit_header' and unit.get('unit') == claim.unit
            and unit.get('layout_lane') == context.get('layout_lane')
            and unit.get('start') == title['start'] and unit.get('end') == title['end']
            and unit.get('text') == title['text']):
        fail('券商残项缺少完整原文摘要、列头、单位或表格边界证据')
    if (type(column) is int and isinstance(headers, list) and 1 <= column <= len(headers)
            and (not _source_ref_valid(headers[column-1])
                 or not re.fullmatch(claim.period[:4] + 'A', headers[column-1]['text'].strip()))):
        fail('券商残项列头没有证明该历史期间；预测列不可作为历史事实')
    if claim.parse_status != 'parsed' or claim.modality != 'fact' or claim.relation != 'value':
        fail('券商余额残项仅接受已解析的历史金额单元格')

    same_table = []
    for sibling in source_claims:
        other = sibling.table_context or {}
        if (sibling.company == claim.company and sibling.period == claim.period
                and sibling.unit == claim.unit and other.get('title_source') == title
                and other.get('headers') == headers and other.get('summary_source') == context.get('summary_source')
                and other.get('layout_lane') == context.get('layout_lane')
                and other.get('column_index') == column
                and other.get('layout_source') == context.get('layout_source')):
            same_table.append(sibling)
    required = (*layout['rows'], layout['boundary'])
    selected = []
    for caption, expected_account in required:
        matches = [row for row in same_table
                   if _caption((row.table_context or {}).get('row_label')) == caption]
        if len(matches) != 1:
            fail('券商残项完整组成行缺失或重复；不得省略扣项或默认零',
                 account=expected_account, source_caption=caption, occurrences=len(matches))
            continue
        row = matches[0]
        raw = row.table_context or {}
        spans = raw.get('source_spans') or []
        spans = spans if isinstance(spans, list) else []
        cell = spans[column] if type(column) is int and 0 <= column < len(spans) else {}
        label = spans[0] if spans else {}
        cell = cell if isinstance(cell, dict) else {}
        label = label if isinstance(label, dict) else {}
        if not (raw.get('account_hint') == expected_account
                and raw.get('period_hint') == claim.period and raw.get('period_source') == 'source_header'
                and raw.get('source_unit_proof') == context.get('source_unit_proof')
                and row.parse_status == 'parsed' and row.modality == 'fact' and row.relation == 'value'
                and type(label.get('start')) is int and type(label.get('end')) is int
                and label['start'] < label['end'] <= row.start
                and label['end'] - label['start'] == len(raw.get('row_label', ''))
                and type(cell.get('start')) is int and type(cell.get('end')) is int
                and cell['start'] <= row.start < row.end <= cell['end']):
            fail('券商残项组成行的科目、期间、单位或原文定位不一致',
                 account=expected_account, source_caption=caption)
        selected.append(row)
        proof['rows'].append({'caption': caption, 'account': expected_account,
                              'label': {'start': label.get('start'), 'end': label.get('end'),
                                        'text': raw.get('row_label')},
                              'cell': {'start': row.start, 'end': row.end},
                              'is_boundary': (caption, expected_account) == layout['boundary']})
    if len(selected) == len(required):
        positions = [row.start for row in selected]
        if positions != sorted(set(positions)):
            fail('券商残项组成行顺序或表组边界不一致')
        actual = sorted((row for row in same_table if positions[0] <= row.start < positions[-1]),
                        key=lambda row: row.start)
        if [(row.table_context or {}).get('row_label', '').strip() for row in actual] != [x[0] for x in layout['rows']]:
            fail('券商残项表组含有未登记的额外组成行；当前公式不能覆盖该布局')
    proof['status'] = 'incomplete' if proof['blockers'] else 'complete'
    proof['sha256'] = _source_digest(proof)
    return proof


def attach_broker_compositions(claims):
    """Attach source composition once, before semantic binding/freezing."""
    result = []
    for claim in claims:
        if 'source_dimensions' in (claim.table_context or {}):
            result.append(claim)
            continue
        proof = broker_source_composition(claim, claims)
        result.append(replace(claim, table_context={**claim.table_context, 'broker_composition': proof})
                      if proof is not None else claim)
    return result


def validated_broker_composition(claim):
    """Validate host-frozen composition without consuming a candidate value."""
    proof = deepcopy((claim.table_context or {}).get('broker_composition'))
    if not isinstance(proof, dict):
        return None
    recorded = proof.pop('sha256', None)
    if (recorded != _source_digest(proof) or proof.get('schema_version') != 'source-broker-composition.v1'
            or proof.get('source_identity') != _source_broker_identity(claim)
            or proof.get('numeric_authority') != 'external_facts_only'
            or proof.get('cash_or_investment_definition_selected') is not False):
        return None
    layout = BROKER_COMPOSITIONS.get(proof.get('account'))
    if not layout or proof.get('formula') != layout['formula']:
        return None
    proof['sha256'] = recorded
    return proof


@dataclass
class DerivedMetric:
    value: Decimal | None = None
    interval: DecimalInterval | None = None
    operands: list[Fact] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    rule: dict = field(default_factory=dict)
    abstention_code: str | None = None
    reason: str | None = None
    blockers: list[dict] = field(default_factory=list)
    calculation_steps: list[dict] = field(default_factory=list)

    @property
    def formula(self):
        return self.rule.get('formula')

    @property
    def rule_ids(self):
        return list(dict.fromkeys([*self.rule.get('dependency_rule_ids', []),
                                  *([self.rule['id']] if self.rule else [])]))


def _caption(text):
    return re.sub(r'\s+', '', re.sub(r'[（(](?:元|万元|亿元|股|万股|亿股)[）)]$', '', text or ''))


def _same_generation(left, right, *, permit_dimension_difference=False, permit_adjusted_comparative=False):
    # Merely sharing a date or version rank is insufficient. Both operands
    # must identify the same report and the same actual column version.
    versions_match=left.version==right.version
    if (permit_adjusted_comparative and left.period!=right.period
            and left.version.split('/',1)[0]==right.version.split('/',1)[0]
            and left.version_group and left.version_group==right.version_group):
        def supported_column(fact):
            suffix=fact.version.partition('/')[2]
            return not suffix or (suffix in {'调整后','重述后','更正后'} and suffix in fact.column_label)
        versions_match=supported_column(left) and supported_column(right)
    return (bool(left.source_doc or left.source_path)
            and (left.source_doc or left.source_path) == (right.source_doc or right.source_path)
            and (left.company, left.scope, left.nr, left.publish_date, left.version_group)
            == (right.company, right.scope, right.nr, right.publish_date, right.version_group)
            and versions_match
            and left.currency == right.currency
            and (permit_dimension_difference or
                 (left.dimension_kind,left.dimension_id)==(right.dimension_kind,right.dimension_id)))


def derive_registered_metric(store, company, account, period, scope, nr, report_date, *,
                             dimension_kind='total',dimension_id='',currency='CNY',definition_id='',
                             strategy='latest_as_of',source_doc=None,min_confidence='0.95',
                             source_claim=None,_dependency_path=()):
    """All metric callers use this one source-bound dependency executor."""
    from .metrics import metric_spec
    out=DerivedMetric()
    spec=metric_spec(account,definition_id)
    def reject(code,reason):
        out.value,out.interval=None,None
        out.abstention_code,out.reason=code,reason
        out.blockers.append({'code':code,'kind':'formula_precondition','reason':reason})
        return out
    if spec is None:
        return reject('X2','指标定义没有匹配的授权方法；不得按数值选择公式')
    if account in _dependency_path or len(_dependency_path) >= 4:
        return reject('X2','登记公式依赖存在循环或超过受控深度')
    if not report_date:
        return reject('X5','授权指标计算缺少披露截止日期')
    if scope not in {'合并','母公司'} or nr not in {'未扣非','扣非'}:
        return reject('X3','授权指标计算报表或扣非口径未知')
    if scope not in spec.scopes:
        return reject('X2','登记公式不适用于该报表口径；母公司权益不能从归母与少数股东权益合成')
    direct = store.query_outcome(company,account,period,scope,nr,cutoff=report_date,
               strategy=strategy,source_doc=source_doc,min_confidence=min_confidence,
               dimension_kind=dimension_kind,dimension_id=dimension_id,currency=currency,
               definition_id=definition_id if spec.definition_required else '')
    if direct.status != 'missing':
        out.evidence.extend(direct.evidence)
        return reject(direct.abstention_code or 'X2',direct.reason or '已有原始披露不能被派生公式替换')
    # Ratios based on issuer-wide denominators require a genuine part identity.
    if account=='REVENUE_SHARE' and dimension_kind=='total':
        return reject('X3','收入占比缺少来源明确的分业务或客户分子维度')
    # A MetricSpec can be registered before its professional rule card is
    # signed.  Treat that state as an explicit, auditable formula abstention
    # instead of raising StopIteration and aborting an otherwise usable
    # financial-analysis run.
    card=next((x for x in load_rule_cards() if x['id']==spec.rule_id), None)
    if card is None:
        return reject('X2', f'指标 {account} 已登记但缺少对应规则卡 {spec.rule_id}')
    out.rule={key:card[key] for key in ('id','version','version_hash','formula','dimension','inputs','positive_inputs')}
    out.rule['preconditions']=['同公司、报表和扣非口径、币种、披露来源及版本',
                                '每项操作数独立有证据；缺失不默认零；拒绝冲突、低置信与未知披露日']
    values,intervals,input_sources={},{},{}
    source=source_doc
    failures=[]
    if account in BROKER_COMPOSITIONS:
        expected_formula = BROKER_COMPOSITIONS[account]['formula']
        if (card['formula'] != expected_formula
                or set(card['inputs']) != {item.name for item in spec.inputs}):
            failures.append({'code': 'X2', 'kind': 'registry_composition', 'account': account,
                'reason': '券商残项规则卡与登记的完整组成不一致；拒绝使用遗漏扣项的旧公式',
                'required_formula': expected_formula, 'registered_formula': card['formula']})
        if source_claim is not None:
            proof = validated_broker_composition(source_claim)
            if (proof is None or proof.get('account') != account
                    or (source_claim.company, source_claim.period) != (company, period)
                    or dimension_kind != 'total' or dimension_id):
                failures.append({'code': 'X3', 'kind': 'source_composition', 'account': account,
                    'reason': '券商残项未取得与当前原文公司、期间及表组一致的完整组成证明'})
            else:
                out.rule['source_composition'] = deepcopy(proof)
                failures.extend(proof.get('blockers', []))
                if proof.get('status') != 'complete' and not proof.get('blockers'):
                    failures.append({'code': 'X3', 'kind': 'source_composition', 'account': account,
                        'reason': '券商残项组成证明状态不完整'})
    for item in spec.inputs:
        from .taxonomy import UNDEFINED_ACCOUNTS
        if item.account in UNDEFINED_ACCOUNTS:
            failures.append({'code':'X2','kind':'definition','account':item.account,'period':period,
                    'reason':'券商简表分项现金或长期投资缺少来源明确的组成定义；不能替代为相近法定科目'})
            continue
        target_period=period
        if item.prior_year:
            if not re.fullmatch(r'20\d{2}Y',period or ''):
                failures.append({'code':'X2','kind':'formula_precondition','reason':'平均余额周转率目前仅授权完整年度；不静默年化半年度','account':item.account,'period':period})
                continue
            target_period=str(int(period[:4])-1)+'Y'
        dim_kind='total' if item.dimension=='total' else dimension_kind
        dim_id='' if item.dimension=='total' else dimension_id
        query=store.query_outcome(company,item.account,target_period,scope,item.nr or nr,cutoff=report_date,
                    strategy='same_report' if source else strategy,source_doc=source,
                    min_confidence=min_confidence,dimension_kind=dim_kind,dimension_id=dim_id,currency=currency)
        # A same-report lookup can return ``missing`` when a fact exists in a
        # different report.  Preserve that distinction: silently treating the
        # source-filtered miss as absent would turn a cross-report splice into
        # X1 and hide a material generation error.  Probe the unrestricted
        # snapshot only for diagnostics; its value is never consumed as an
        # operand.  A visible alternative therefore becomes X4, while a true
        # absence remains X1 below.
        if query.status == 'missing' and source:
            unrestricted = store.query_outcome(
                company, item.account, target_period, scope, item.nr or nr,
                cutoff=report_date, strategy='latest_as_of', source_doc=None,
                min_confidence=min_confidence, dimension_kind=dim_kind,
                dimension_id=dim_id, currency=currency)
            if unrestricted.status != 'missing':
                # Legacy structured fixtures may carry human-readable source
                # labels without a frozen PDF path/hash.  They represent an
                # unavailable operand (X1); real frozen materials always
                # carry a path or a 12+ hex document hash and remain X4 on a
                # cross-report splice.
                candidate = unrestricted.fact
                synthetic = bool(candidate) and not candidate.source_path and not re.fullmatch(
                    r'[0-9a-fA-F]{12,64}', candidate.source_doc or '')
                code = 'X1' if synthetic else (unrestricted.abstention_code or 'X4')
                failures.append({'code': code, 'kind': 'operand_source',
                                 'account': item.account, 'period': target_period,
                                 'reason': '指标操作数存在于其他报告或版本；禁止跨报告拼接',
                                 'lookup_status': unrestricted.status,
                                 'evidence': list(unrestricted.evidence)})
                out.evidence.extend(unrestricted.evidence)
                continue
        if query.status=='missing' and metric_spec(item.account) is not None:
            nested=derive_registered_metric(store,company,item.account,target_period,scope,item.nr or nr,report_date,
                    strategy='same_report' if source else strategy,source_doc=source,min_confidence=min_confidence,
                    dimension_kind=dim_kind,dimension_id=dim_id,currency=currency,
                    _dependency_path=(*_dependency_path,account))
            out.operands.extend(nested.operands);out.evidence.extend(nested.evidence)
            out.calculation_steps.extend(nested.calculation_steps)
            out.rule.setdefault('dependency_rule_ids',[]).extend(nested.rule_ids)
            if nested.value is None:
                failures.extend(nested.blockers or [{'code':nested.abstention_code or 'X1','kind':'operand',
                    'account':item.account,'period':target_period,'reason':nested.reason,'lookup_status':'derived_unresolved'}])
                continue
            if not source and nested.operands:
                source=nested.operands[0].source_doc or nested.operands[0].source_path
            values[item.name]=nested.value;intervals[item.name]=nested.interval
            input_sources[item.name]={'account':item.account,'source_fact_ids':[fact.fact_id for fact in nested.operands],
                                       'derived_rule_id':nested.rule['id']}
            continue
        if not query.fact:
            failures.append({'code':query.abstention_code or 'X1','kind':'operand','account':item.account,
                             'period':target_period,'reason':query.reason,'lookup_status':query.status,'evidence':list(query.evidence)})
            out.evidence.extend(query.evidence)
            continue
        fact=query.fact
        out.operands.append(fact);out.evidence.extend(fact.evidence)
        if not source:
            source=fact.source_doc or fact.source_path
        values[item.name]=fact.value;intervals[item.name]=fact_interval(fact)
        input_sources[item.name]={'account':item.account,'source_fact_ids':[fact.fact_id]}
    out.operands=list({fact.fact_id:fact for fact in out.operands}.values())
    out.evidence=list(dict.fromkeys(out.evidence))
    out.blockers=failures
    if failures:
        primary=next((x for x in failures if x['code']=='X4'),failures[0])
        out.abstention_code,out.reason=primary['code'],primary['reason']
        return out
    if not out.operands or any(not x.evidence or not (x.source_doc or x.source_path) for x in out.operands):
        return reject('X4','指标操作数缺少来源报告或证据，不能锁定同一披露版本')
    if any(not _same_generation(out.operands[0],x,permit_dimension_difference=account=='REVENUE_SHARE',permit_adjusted_comparative=any(i.prior_year for i in spec.inputs)) for x in out.operands[1:]):
        return reject('X4','指标操作数不属于同报告、披露日期及原始列版本')
    out.value=execute_rule(spec.rule_id,values)
    out.interval=execute_rule_interval(spec.rule_id,intervals)
    if out.value is None or out.interval is None:
        return reject('X2','授权公式前提不成立；分母披露区间须严格为正')
    out.calculation_steps.append({'rule_id':card['id'],'rule_version':card['version'],
        'rule_version_hash':card['version_hash'],'formula':card['formula'],'dimension':card['dimension'],
        'inputs':{key:{**input_sources[key],'value':str(values[key]),'interval':intervals[key].to_dict()} for key in values},
        'value':str(out.value),'interval':out.interval.to_dict()})
    return out


def supported_quarter_periods(store: FactStore, company: str, periods, scope: str) -> set[str]:
    """Expose potential quarters only where the selected reports contain a pair.

    This is a dimension inventory; derive_metric still applies all date,
    confidence, conflict and rule checks before producing any value.
    """
    selected = set(periods)
    result = set()
    for first in store.facts:
        if (first.source_relation != 'value' or first.company != company or first.scope != scope
                or first.account not in FLOW_ACCOUNTS or first.period not in selected):
            continue
        for quarter, (suffix, previous) in QUARTER_INPUTS.items():
            if first.period[4:] != suffix:
                continue
            if any(second.source_relation == 'value' and second.period == first.period[:4]+previous and second.account == first.account
                   and _same_generation(first, second) for second in store.facts):
                result.add(first.period[:4]+quarter)
    return result


def derive_metric(store: FactStore, company: str, account: str, period: str, scope: str, nr: str,
                  report_date: str | None, *, direct_outcome: FactQueryOutcome | None = None,
                  strategy: str = 'latest_as_of', source_doc: str | None = None,
                  min_confidence: str = '0.95', basic_eps_definition: bool = False,
                  dimension_kind='total',dimension_id='',currency='CNY',definition_id='',
                  source_claim=None) -> DerivedMetric:
    """Calculate only after a genuine missing direct disclosure.

    Any rejected confidence, conflict, unknown date or source ambiguity stays
    rejected. No caller may use a formula to override that raw lookup.
    """
    out = DerivedMetric()
    def reject(code, reason, extra=()):
        out.value,out.interval=None,None
        out.abstention_code, out.reason = code, reason
        out.evidence = list(dict.fromkeys([*out.evidence, *extra]))
        return out
    if not report_date:
        return reject('X5', '授权派生计算缺少披露截止日期')
    if scope not in {'合并', '母公司'} or nr not in {'未扣非', '扣非'}:
        return reject('X3', '授权派生计算口径未知')
    # A caller-supplied lookup has no dimension proof. Always repeat the raw
    # query here; an unrelated/stale "missing" must not bypass a direct X4.
    from .metrics import METRIC_SPECS
    direct_definition=definition_id if account in METRIC_SPECS and METRIC_SPECS[account].definition_required else ''
    direct = store.query_outcome(
        company, account, period, scope, nr, cutoff=report_date, strategy=strategy,
        source_doc=source_doc, min_confidence=min_confidence, dimension_kind=dimension_kind,
        dimension_id=dimension_id,currency=currency,definition_id=direct_definition)
    if direct.status != 'missing':
        return reject(direct.abstention_code or 'X2', direct.reason or '已披露值不能被派生值替换', direct.evidence)
    suffix = period[4:] if re.fullmatch(r'\d{4}(?:Y|H1|9M|Q[1-4])', period or '') else ''
    if account == 'EPS':
        rule_id = 'FIN-EPS'
        if not basic_eps_definition or nr != '未扣非':
            return reject('X2', '仅明确定义的基本每股收益可派生；未定义EPS、扣非或稀释口径不适用')
        keys, periods = ('NP_PARENT', 'WEIGHTED_SHARES'), (period, period)
    elif account in FLOW_ACCOUNTS and suffix in QUARTER_INPUTS:
        rule_id = 'FIN-single-quarter'
        keys = ('cumulative', 'prior_cumulative')
        periods = tuple(period[:4]+value for value in QUARTER_INPUTS[suffix])
    else:
        if account=='NP_PARENT_ADJ' and definition_id=='metric:nri-parent-after-tax':
            return derive_registered_metric(store,company,'NP_PARENT_ADJ_NET',period,scope,'未扣非',report_date,
                     strategy=strategy,source_doc=source_doc,min_confidence=min_confidence,
                     dimension_kind=dimension_kind,dimension_id=dimension_id,currency=currency,definition_id=definition_id)
        return derive_registered_metric(store,company,account,period,scope,nr,report_date,
                 strategy=strategy,source_doc=source_doc,min_confidence=min_confidence,
                 dimension_kind=dimension_kind,dimension_id=dimension_id,currency=currency,
                 definition_id=definition_id,source_claim=source_claim)
    card = next(row for row in load_rule_cards() if row['id'] == rule_id)
    out.rule = {key: card[key] for key in ('id', 'version', 'version_hash', 'formula', 'dimension', 'inputs', 'positive_inputs')}
    out.rule['preconditions'] = ['同公司、报表口径、扣非口径、同报告、披露日期及原始列版本',
                                  '仅真实缺失允许派生；拒绝冲突、低置信与未知披露日']
    anchor_account = 'NP_PARENT_ORDINARY' if account == 'EPS' else account
    first = store.query_outcome(company, anchor_account, periods[0], scope, nr, cutoff=report_date,
                                strategy=strategy, source_doc=source_doc, min_confidence=min_confidence,
                                dimension_kind=dimension_kind,dimension_id=dimension_id,currency=currency)
    if account == 'EPS' and first.status == 'missing':
        # Legacy structured indexes may use the older broad account, but
        # still need an exact source-written ordinary-share numerator label.
        # A rejected independent ordinary lookup cannot fall through here.
        first = store.query_outcome(company, 'NP_PARENT', periods[0], scope, nr, cutoff=report_date,
                                    strategy=strategy, source_doc=source_doc, min_confidence=min_confidence,
                                    dimension_kind=dimension_kind,dimension_id=dimension_id,currency=currency)
    if not first.fact:
        return reject(first.abstention_code or 'X1', first.reason or '缺少首项操作数', first.evidence)
    anchor = first.fact
    out.operands.append(anchor)
    out.evidence.extend(anchor.evidence)
    document = anchor.source_doc or anchor.source_path
    if not document or not anchor.evidence:
        return reject('X4', '派生操作数缺少来源报告或证据，不能锁定同一披露版本')
    second_account = 'WEIGHTED_SHARES' if account == 'EPS' else account
    second = store.query_outcome(company, second_account, periods[1], scope, nr, cutoff=report_date,
                                 strategy='same_report', source_doc=document, min_confidence=min_confidence,
                                 dimension_kind=dimension_kind,dimension_id=dimension_id,currency=currency)
    if not second.fact:
        return reject(second.abstention_code or 'X1', second.reason or '同报告缺少第二项操作数', second.evidence)
    out.operands.append(second.fact)
    out.rule['input_accounts'] = {key: fact.account for key, fact in zip(keys, out.operands)}
    out.evidence = list(dict.fromkeys([*out.evidence, *second.fact.evidence]))
    if not second.fact.evidence or not _same_generation(anchor, second.fact):
        return reject('X4', '派生操作数来源、披露日或原始列版本不一致，禁止跨版本拼接')
    currency_units = {'元', '万元', '百万元', '亿元'}
    if account == 'EPS':
        out.rule['preconditions'].extend(['分子明示归属于普通股股东的净利润，不默认全额归母利润',
                                         '分母明示发行在外普通股加权平均数，正区间；期末股数与稀释股数不可替代'])
        if _caption(anchor.label) not in ORDINARY_PROFIT_LABELS:
            return reject('X2', '分子未证明为归属于普通股股东的净利润；优先股股息或永续资本影响不能默认零')
        if _caption(second.fact.label) not in WEIGHTED_SHARE_LABELS or second.fact.display_unit not in {'股', '万股', '亿股'}:
            return reject('X2', '分母没有明确发行在外普通股加权平均数及股数单位，禁止期末股本或稀释股数替代')
        if anchor.display_unit not in currency_units:
            return reject('X4', '基本EPS分子不是明确货币金额单位')
    elif any(fact.display_unit not in currency_units for fact in out.operands):
        return reject('X4', '单季流量操作数不是明确货币金额单位；存量、比例和每股指标不可累计相减')
    amounts = {key: fact.value for key, fact in zip(keys, out.operands)}
    intervals = {key: fact_interval(fact) for key, fact in zip(keys, out.operands)}
    out.interval = execute_rule_interval(rule_id, intervals)
    out.value = execute_rule(rule_id, amounts) if out.interval is not None else None
    if out.value is None:
        return reject('X2', '授权派生公式前提不成立；分母区间为零、负值或触零')
    return out
