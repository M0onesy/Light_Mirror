import pytest
from mingjing.source_semantics import _table_title_subject


@pytest.mark.parametrize('title', [
    '表4：2023H1公司主要客户', '表13：公司通过共建模式获取算力资源',
    '表13：公司向乙公司采购算力资源', '表4：公司从乙公司采购资源的收入构成',
    '表4：客户营业收入', '表4：年度客户情况',
])
def test_generic_issuer_and_counterparty_caption_do_not_introduce_another_issuer(title):
    assert _table_title_subject({'title':title}, '并行科技') is None


@pytest.mark.parametrize('title', [
    '表4：2023H1乙公司主要客户', '表13：海光信息公司通过共建模式获取资源',
    '表13：海光信息通过共建模式获取资源', '表4：2023年乙公司前五大客户',
])
def test_named_other_issuer_is_rejected_before_row_identity(title):
    assert _table_title_subject({'title':title}, '并行科技')['source_company_conflict'] is True


def test_parallel_issuer_title_is_ambiguous_and_exact_alias_is_allowed():
    assert _table_title_subject({'title':'表4：并行科技与乙公司主要客户'}, '并行科技')['source_company_ambiguous'] is True
    assert _table_title_subject({'title':'表4：北京并行科技股份有限公司主要客户'}, '并行科技')['company']=='并行科技'
