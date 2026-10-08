from mingjing.parser import _row_account


def test_weighted_roe_wrapped_definition_is_one_row():
    ordinary = _row_account(
        "加权平均净资产收益率%（依据\n"
        "归属于上市公司股东的净利润\n计算）\n-63.55% -65.05%"
    )
    assert ordinary is not None
    assert ordinary[1] == "ROE"
    assert ordinary[2] == "-63.55%-65.05%"

    adjusted = _row_account(
        "加权平均净资产收益率%（依据\n"
        "归属于上市公司股东的扣除非\n经常性损益后的净利润计算）\n"
        "-68.55% -65.72%"
    )
    assert adjusted is not None
    assert adjusted[1] == "ROE"
    assert adjusted[2] == "-68.55%-65.72%"


def test_row_account_still_rejects_prose_after_compaction():
    assert _row_account("加权平均净资产收益率（依据\n公司经营情况分析）") is None
