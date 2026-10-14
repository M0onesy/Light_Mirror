"""C 部分验收：口径映射 registry 的"无出处即拒绝"。

判据（对齐 ac-refactor-plan §2）：
1. 空 registry 不放行（维持弃权）；
2. 缺出处字段不放行；
3. text_sha256 与 source_quote 不符不放行；
4. definition_id 与 METRIC_SPECS 不一致不放行；
5. provenance 非原文枚举不放行；
6. 出处齐备且自洽时才放行，并在 judge 链路消除来源定义缺口。
"""
import hashlib
from dataclasses import replace

from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing import source_method_registry as reg
from mingjing.source_method_registry import (
    SCHEMA, _mapping_evidence_ok, registry, validated_mapping,
)
from mingjing.source_semantics import (
    freeze_source_dimensions, unresolved_source_definition_issue,
)

QUOTE = '速动比率=速动资产/流动负债'
SPAN = {'start': 0, 'end': len(QUOTE), 'text': QUOTE}


def _mapping(**overrides):
    base = {
        'schema_version': SCHEMA,
        'source_account': 'QUICK_RATIO_UNSPECIFIED',
        'canonical_account': 'QUICK_RATIO',
        'definition_id': 'metric:quick-ratio-inventory',
        'rule_id': 'FIN-quick-ratio',
        'source_page': 42,
        'source_quote': QUOTE,
        'source_span': dict(SPAN),
        'report_date': '2024-04-22',
        'text_sha256': hashlib.sha256(QUOTE.encode('utf-8')).hexdigest(),
        'provenance': 'report_original_footnote',
    }
    base.update(overrides)
    return base


def _roe_claim():
    """构造一个语义复核待定的来源未定 claim（QUICK_RATIO_UNSPECIFIED），并冻结来源维度。"""
    claim = extract_mentions('测试公司2023年速动比率为1.5倍。', '测试公司')[-1]
    ctx = dict(claim.table_context)
    ctx.update(numeric_parse_status='parsed', semantic_review_required=True,
               source_account_unresolved='QUICK_RATIO_UNSPECIFIED')
    return freeze_source_dimensions(replace(claim, table_context=ctx, parse_status='parsed'))


def test_registry_starts_empty():
    """护城河：未取证前 registry 必须为空。"""
    assert registry == []


def test_empty_registry_does_not_allow():
    claim = _roe_claim()
    assert validated_mapping(claim) is None
    assert unresolved_source_definition_issue(claim) is not None


def test_missing_evidence_field_is_rejected():
    for key in ('source_page', 'source_quote', 'source_span', 'report_date',
                'text_sha256', 'provenance'):
        row = _mapping()
        del row[key]
        assert _mapping_evidence_ok(row) is False, key


def test_wrong_hash_is_rejected():
    assert _mapping_evidence_ok(_mapping(text_sha256='0' * 64)) is False


def test_span_text_must_match_quote():
    row = _mapping(source_span={'start': 0, 'end': 3, 'text': '错文本'})
    assert _mapping_evidence_ok(row) is False


def test_definition_id_must_be_registered():
    assert _mapping_evidence_ok(_mapping(definition_id='metric:not-registered')) is False


def test_non_source_provenance_is_rejected():
    assert _mapping_evidence_ok(_mapping(provenance='user_supplied')) is False


def test_complete_evidence_is_accepted_and_allows(monkeypatch):
    monkeypatch.setattr(reg, 'registry', [_mapping()])
    claim = _roe_claim()
    # 出处齐备才放行
    assert validated_mapping(claim) is not None
    assert unresolved_source_definition_issue(claim) is None


def test_mismatched_source_account_does_not_match(monkeypatch):
    monkeypatch.setattr(reg, 'registry', [_mapping(source_account='NET_MARGIN_UNSPECIFIED')])
    assert validated_mapping(_roe_claim()) is None


def test_empty_registry_judge_still_abstains():
    """空表下 judge 链路维持弃权（护城河回归）。"""
    result = Judge(FactStore([]), '2024-04-22').verify(_roe_claim())
    assert result.status == 'abstained'


# ---- 方向 B：出处真实性（抗"一源多标"凑数）---------------------------------

def _mapping_for(canonical_account, definition_id, quote, page=42, account=None):
    """构造一条挂到指定原文片段上的映射。"""
    span = {'start': 0, 'end': len(quote), 'text': quote}
    return {
        'schema_version': SCHEMA,
        'source_account': account or f'{canonical_account}_UNSPECIFIED',
        'canonical_account': canonical_account,
        'definition_id': definition_id,
        'rule_id': None,
        'source_page': page,
        'source_quote': quote,
        'source_span': span,
        'report_date': '2024-04-22',
        'text_sha256': hashlib.sha256(quote.encode('utf-8')).hexdigest(),
        'provenance': 'report_original_footnote',
    }


def test_shared_source_across_accounts_is_rejected(monkeypatch):
    """同一原文片段被过多互异指标共用 → 判定为凑数，整组拒绝。"""
    from mingjing.metrics import METRIC_SPECS
    from mingjing.source_method_registry import MAX_ACCOUNTS_PER_SOURCE
    # 构造 N 条不相关指标共用同一段通用规定（仿 caliber.json 的"一源多标"）
    quote = '适用范围为所有报表项目。'
    accounts = [a for a in ('QUICK_RATIO', 'AP_TURNOVER', 'AR_TURNOVER', 'ASSET_TURNOVER')
                if a in METRIC_SPECS and METRIC_SPECS[a].definition_id]
    assert len(accounts) > MAX_ACCOUNTS_PER_SOURCE, '测试前提：需多于阈值的登记指标'
    rows = [_mapping_for(a, METRIC_SPECS[a].definition_id, quote) for a in accounts]
    monkeypatch.setattr(reg, 'registry', rows)
    # 每一条都因共用出处的条目过多而被拒
    for row in rows:
        assert _mapping_evidence_ok(row) is False, row['canonical_account']


def test_distinct_sources_are_accepted(monkeypatch):
    """每指标锚定各自独立原文 → 通过（对照：证明阈值只拦公用，不拦正常映射）。"""
    quote_a = '速动比率=速动资产/流动负债'
    row = _mapping_for('QUICK_RATIO', 'metric:quick-ratio-inventory', quote_a)
    monkeypatch.setattr(reg, 'registry', [row])
    assert _mapping_evidence_ok(row) is True


def test_shared_source_within_threshold_is_accepted(monkeypatch):
    """共用未超阈值（≤MAX_ACCOUNTS_PER_SOURCE 个互异指标）仍放行。"""
    from mingjing.metrics import METRIC_SPECS
    from mingjing.source_method_registry import MAX_ACCOUNTS_PER_SOURCE
    quote = '同一段原文可支撑的少量指标。'
    accounts = [a for a in ('QUICK_RATIO', 'AP_TURNOVER') if a in METRIC_SPECS and METRIC_SPECS[a].definition_id]
    assert len(accounts) <= MAX_ACCOUNTS_PER_SOURCE
    rows = [_mapping_for(a, METRIC_SPECS[a].definition_id, quote) for a in accounts]
    monkeypatch.setattr(reg, 'registry', rows)
    for row in rows:
        assert _mapping_evidence_ok(row) is True, row['canonical_account']
